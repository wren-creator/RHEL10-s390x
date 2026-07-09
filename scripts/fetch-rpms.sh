#!/usr/bin/env bash
# fetch-rpms.sh — harvest s390x RPMs (+ full dependency tree) using your own
# Red Hat account, without needing an already-entitled build host.
#
# How it works: pulls a throwaway UBI container run as linux/s390x under
# QEMU emulation (subscription-manager only exposes s390x repos to an actual
# s390x system — it filters client-side by platform arch, so an x86_64
# container cannot see them no matter what facts it claims). Registers it
# against YOUR Red Hat account, discovers the s390x BaseOS/AppStream repos,
# downloads the requested packages + all their dependencies, builds repo
# metadata with createrepo_c, then unregisters the container (trap-
# guaranteed, even on failure) before it's discarded with --rm.
# Requires QEMU s390x binfmt on the host — the same layer the Studio's
# cross-builds already use (Prepare Build Engine sets it up).
#
# Also logs in to registry.redhat.io on the host (reusing the same username)
# before pulling the harvester image, and — if your network intercepts TLS
# through a corporate root CA — trusts that CA inside the throwaway container
# so subscription-manager/dnf can validate Red Hat's cert.
#
# Nothing is registered or installed on your actual host — only inside the
# disposable container. Your password is prompted by subscription-manager
# and by the registry login themselves; this script never sees or stores it.
#
# Usage:
#   ./scripts/fetch-rpms.sh                        # default s390x package list
#   ./scripts/fetch-rpms.sh --packages "vim,curl"   # override the package set
#   ./scripts/fetch-rpms.sh --dest /path/to/cache   # override the output dir
#   ./scripts/fetch-rpms.sh --ca-cert /etc/ssl/certs/corp-root-ca.pem
#
# Auth:
#   Interactive (default) — prompts for your Red Hat username; password is
#   prompted separately by registry login and by subscription-manager inside
#   the container.
#   Non-interactive — set RH_ORG and RH_ACTIVATION_KEY (an activation key on
#   your account/org) for subscription registration; registry.redhat.io login
#   is skipped in this mode (no interactive credential to use).
#
# Corporate TLS-inspecting proxy: pass --ca-cert /path/to/your-root-ca.pem
# (or set CA_CERT_FILE) if subscription-manager/dnf inside the container
# cannot validate Red Hat's TLS cert. It is mounted read-only and trusted
# via update-ca-trust inside the disposable container only.
#
# Output: a local dnf repo (RPMs + repodata/) under rpm-cache/s390x/ (or
# --dest). Point a Containerfile's local.repo baseurl at file://<that dir>
# to install fully offline afterward — no entitlement needed at build time.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_LIST_FILE="$HERE/scripts/package-list.s390x.txt"
CACHE_DIR="${RPM_CACHE_DIR:-$HERE/rpm-cache/s390x}"
UBI_IMAGE="${UBI_IMAGE:-registry.access.redhat.com/ubi10/ubi:latest}"
CA_CERT_FILE="${CA_CERT_FILE:-}"

RED='\033[0;31m'; YEL='\033[1;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GRN}[+]${NC} $*"; }
warn() { echo -e "${YEL}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
step() { echo -e "\n${CYN}══${NC} $* ${CYN}══${NC}"; }

PACKAGES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --packages) PACKAGES="$2"; shift 2 ;;
    --dest)     CACHE_DIR="$2"; shift 2 ;;
    --ca-cert)  CA_CERT_FILE="$2"; shift 2 ;;
    -h|--help)  sed -n '2,46p' "$0"; exit 0 ;;
    *) err "Unknown argument: $1 (see --help)" ;;
  esac
done

if [ -n "$CA_CERT_FILE" ]; then
  [ -f "$CA_CERT_FILE" ] || err "--ca-cert file not found: $CA_CERT_FILE"
fi

step "Pre-flight"

ENGINE="${STUDIO_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  command -v podman >/dev/null 2>&1 && ENGINE=podman
  [ -z "$ENGINE" ] && command -v docker >/dev/null 2>&1 && ENGINE=docker
fi
[ -n "$ENGINE" ] || err "Neither podman nor docker found on PATH"
log "Engine: $ENGINE"
log "Harvester image: $UBI_IMAGE"

if [ -n "$PACKAGES" ]; then
  IFS=',' read -r -a PKG_ARR <<< "$PACKAGES"
else
  [ -f "$PKG_LIST_FILE" ] || err "Package list not found: $PKG_LIST_FILE"
  mapfile -t PKG_ARR < <(grep -vE '^\s*(#|$)' "$PKG_LIST_FILE")
fi
[ "${#PKG_ARR[@]}" -gt 0 ] || err "No packages to fetch"
log "Packages (${#PKG_ARR[@]}): ${PKG_ARR[*]}"

mkdir -p "$CACHE_DIR"
log "Output repo dir: $CACHE_DIR"

AUTH_MODE=user
RH_USERNAME="${RH_USERNAME:-}"
if [ -n "${RH_ORG:-}" ] && [ -n "${RH_ACTIVATION_KEY:-}" ]; then
  log "Auth: activation key (org=$RH_ORG) — non-interactive"
  AUTH_MODE=key
else
  [ -t 0 ] || err "No TTY for interactive registration — set RH_ORG + RH_ACTIVATION_KEY for non-interactive use"
  read -rp "Red Hat account username: " RH_USERNAME
  [ -n "$RH_USERNAME" ] || err "Username required"
  log "Auth: username/password (password prompted inside the container, not seen by this script)"
fi

step "Registry login (registry.redhat.io)"
if [ -n "$RH_USERNAME" ]; then
  log "Logging in as $RH_USERNAME (separate password prompt from subscription-manager's)..."
  "$ENGINE" login registry.redhat.io --username "$RH_USERNAME" \
    || warn "registry.redhat.io login failed — continuing; only needed if UBI_IMAGE pulls from registry.redhat.io"
else
  warn "No username available in non-interactive (activation-key) mode — skipping registry.redhat.io login"
fi

if [ -n "$CA_CERT_FILE" ]; then
  log "Corporate CA cert will be trusted inside the harvester container: $CA_CERT_FILE"
fi

# ── Inner script: runs INSIDE the throwaway container ──────────────────────
INNER="$(mktemp)"
trap 'rm -f "$INNER"' EXIT

cat > "$INNER" <<'INNER_EOF'
#!/bin/bash
set -euo pipefail
RED='\033[0;31m'; YEL='\033[1;33m'; GRN='\033[0;32m'; NC='\033[0m'
log()  { echo -e "${GRN}[+]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

unregister() { subscription-manager unregister >/dev/null 2>&1 || true; }
trap unregister EXIT

if [ -f /tmp/corp-ca.pem ]; then
  log "Trusting corporate CA cert (system trust store)..."
  cp /tmp/corp-ca.pem /etc/pki/ca-trust/source/anchors/corp-ca.pem
  update-ca-trust extract
  # subscription-manager does NOT use the system trust store — it validates
  # against *.pem in /etc/rhsm/ca (ca_cert_dir in rhsm.conf). Without this
  # copy, register fails with SSL: CERTIFICATE_VERIFY_FAILED behind a
  # TLS-intercepting proxy even after update-ca-trust.
  log "Trusting corporate CA cert (subscription-manager ca_cert_dir)..."
  mkdir -p /etc/rhsm/ca
  cp /tmp/corp-ca.pem /etc/rhsm/ca/corp-ca.pem
fi

log "Harvester container arch: $(uname -m) (must be s390x for RHSM to expose s390x repos)"

log "Registering with Red Hat..."
if [ "$AUTH_MODE" = "key" ]; then
  subscription-manager register --org="$RH_ORG" --activationkey="$RH_ACTIVATION_KEY"
else
  subscription-manager register --username="$RH_USERNAME"
fi

log "Attaching (no-op on Simple Content Access orgs)..."
subscription-manager attach --auto >/dev/null 2>&1 || true

log "Discovering s390x BaseOS/AppStream repos visible to this account..."
# Match only the standard streams — EUS/E4S/AUS variants would mix update
# streams and cause version skew in the harvested set.
mapfile -t REPO_IDS < <(subscription-manager repos --list 2>/dev/null \
  | awk '/^Repo ID:/{print $3}' \
  | grep -E '^rhel-[0-9]+-for-s390x-(baseos|appstream)-rpms$')
[ "${#REPO_IDS[@]}" -gt 0 ] || err "No s390x BaseOS/AppStream repos visible on this account — confirm it has an s390x-capable subscription attached"
log "Enabling: ${REPO_IDS[*]}"
for r in "${REPO_IDS[@]}"; do subscription-manager repos --enable="$r" >/dev/null; done

log "Ensuring dnf-plugins-core + createrepo_c..."
dnf -y install dnf-plugins-core createrepo_c >/dev/null

log "Downloading ${#PKG_ARR[@]} package(s) + full s390x dependency tree..."
# --disablerepo=ubi-*: resolve purely against the entitled RHEL s390x repos,
# not the UBI subset baked into the harvester image.
dnf download --resolve --alldeps --forcearch=s390x \
    --disablerepo='ubi-*' --destdir=/out "${PKG_ARR[@]}"

log "Building repo metadata..."
createrepo_c /out >/dev/null

log "Harvested $(ls -1 /out/*.rpm 2>/dev/null | wc -l | tr -d ' ') RPM(s) into /out"
INNER_EOF

TTY_ARGS=(-i)
[ -t 0 ] && TTY_ARGS=(-i -t)

# The container runs as linux/s390x under QEMU: subscription-manager filters
# visible repos by the ACTUAL platform arch (client-side, platform.machine()),
# so neither facts overrides nor --forcearch can surface the s390x content
# sets from an x86_64 container. Needs QEMU binfmt for s390x on the host —
# the same layer the Studio's cross-builds already use.
RUN_ARGS=(--rm "${TTY_ARGS[@]}"
  --platform linux/s390x
  -e AUTH_MODE="$AUTH_MODE"
  -e RH_USERNAME="$RH_USERNAME"
  -e RH_ORG="${RH_ORG:-}"
  -e RH_ACTIVATION_KEY="${RH_ACTIVATION_KEY:-}"
  -e PKG_ARR_STR="${PKG_ARR[*]}"
  -v "$INNER:/harvest.sh:ro"
  -v "$CACHE_DIR:/out"
)
[ -n "$CA_CERT_FILE" ] && RUN_ARGS+=(-v "$CA_CERT_FILE:/tmp/corp-ca.pem:ro")

step "Verifying s390x emulation ($ENGINE run --platform linux/s390x)"
EMU_ARCH="$("$ENGINE" run --rm --platform linux/s390x "$UBI_IMAGE" uname -m 2>&1 | tail -1 || true)"
if [ "$EMU_ARCH" != "s390x" ]; then
  err "s390x emulation check failed (got: '$EMU_ARCH', expected: s390x).
    The host lacks QEMU s390x binfmt for $ENGINE — click 'Prepare Build Engine'
    in the Studio, or run:
      sudo podman run --rm --privileged multiarch/qemu-user-static --reset -p yes
    (docker: docker run --privileged --rm tonistiigi/binfmt --install all)
    Note: if you prepared the engine with docker but $ENGINE is podman (or vice
    versa), the emulation may be registered for the other engine's VM — rerun
    the matching command above, or force the engine with STUDIO_ENGINE."
fi
log "Emulation OK — container reports $EMU_ARCH"

step "Registering + harvesting inside a throwaway $UBI_IMAGE container (linux/s390x under QEMU)"
if ! "$ENGINE" run "${RUN_ARGS[@]}" \
  "$UBI_IMAGE" \
  bash -c 'IFS=" " read -r -a PKG_ARR <<< "$PKG_ARR_STR"; source /harvest.sh'; then
  err "Harvest failed — the real cause is in the container output above this line."
fi

step "Done"
log "Local repo ready at: $CACHE_DIR"
log "Point a local.repo baseurl at file://$CACHE_DIR to install fully offline from here on."
