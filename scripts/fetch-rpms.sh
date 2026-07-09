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
#   ./scripts/fetch-rpms.sh --diagnose      # register + report what the
#                                           # subscription can see, no download
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
DIAGNOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --packages) PACKAGES="$2"; shift 2 ;;
    --dest)     CACHE_DIR="$2"; shift 2 ;;
    --ca-cert)  CA_CERT_FILE="$2"; shift 2 ;;
    --diagnose) DIAGNOSE=1; shift ;;
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

subscription_report() {
  echo "──────────────── SUBSCRIPTION DIAGNOSIS ────────────────"
  echo "--- identity (account/org this registration landed in) ---"
  subscription-manager identity 2>&1 || true
  echo "--- status (look for: Content Access Mode) ---"
  subscription-manager status 2>&1 || true
  echo "--- ALL visible repos mentioning s390x (any stream) ---"
  subscription-manager repos --list 2>/dev/null \
    | awk '/^Repo ID:/{print $3}' | grep -i s390x || echo "(none)"
  echo "--- total repos visible to this registration ---"
  subscription-manager repos --list 2>/dev/null | grep -c '^Repo ID:' || true
  echo "--- available pools mentioning s390x / IBM Z ---"
  subscription-manager list --available --all 2>/dev/null \
    | grep -iE -B3 -A6 's390|ibm z|system z' || echo "(none)"
  echo "--- currently consumed subscriptions ---"
  subscription-manager list --consumed 2>/dev/null \
    | grep -iE '^(subscription name|sku|provides arch)' || echo "(none)"
  echo "────────────────────────────────────────────────────────"
}

if [ "${DIAGNOSE:-0}" = "1" ]; then
  subscription_report
  log "Diagnosis complete — no packages downloaded (drop --diagnose to harvest)."
  exit 0
fi

log "Discovering s390x BaseOS/AppStream repos visible to this account..."
# Prefer the standard streams; fall back to EUS when a subscription carries
# only EUS s390x content. Never mix streams in one harvest.
ALL_IDS="$(subscription-manager repos --list 2>/dev/null | awk '/^Repo ID:/{print $3}')"
STREAM=standard
mapfile -t REPO_IDS < <(grep -E '^rhel-[0-9]+-for-s390x-(baseos|appstream)-rpms$' <<<"$ALL_IDS" || true)
if [ "${#REPO_IDS[@]}" -eq 0 ]; then
  mapfile -t REPO_IDS < <(grep -E '^rhel-[0-9]+-for-s390x-(baseos|appstream)-eus-rpms$' <<<"$ALL_IDS" || true)
  if [ "${#REPO_IDS[@]}" -gt 0 ]; then
    STREAM=eus
    log "Standard streams not in this subscription — using EUS streams"
  fi
fi
if [ "${#REPO_IDS[@]}" -eq 0 ]; then
  subscription_report
  err "No s390x BaseOS/AppStream repos matched (standard or EUS). The report
    above shows what this registration CAN see — if s390x repos appear there
    under other names, the filter can be widened; if none appear at all, the
    account/org has no s390x entitlement attached."
fi
log "Enabling: ${REPO_IDS[*]}"
for r in "${REPO_IDS[@]}"; do subscription-manager repos --enable="$r" >/dev/null; done

# EUS content lives under per-minor-release CDN paths (e.g. .../rhel10/10.2/...),
# so the generic releasever "10" would 404 — pin to the newest minor Red Hat
# publishes for this stream.
RELEASEVER_ARGS=()
if [ "$STREAM" = "eus" ]; then
  log "EUS stream — probing the CDN for a published minor release..."
  major="$(sed -E 's/^rhel-([0-9]+)-.*/\1/' <<< "${REPO_IDS[0]}")"
  cert="$(find /etc/pki/entitlement -name '*.pem' ! -name '*-key.pem' 2>/dev/null | head -1 || true)"
  key="${cert%.pem}-key.pem"
  [ -n "$cert" ] && [ -f "$key" ] || err "No entitlement cert/key pair found in /etc/pki/entitlement after registration"

  # Take the baseos repo's real baseurl template from the redhat.repo that
  # subscription-manager just wrote — no path guessing.
  baseos_id="$(printf '%s\n' "${REPO_IDS[@]}" | grep baseos | head -1)"
  base_tpl="$(awk -v id="[$baseos_id]" '$0==id{f=1;next} f&&/^\[/{exit} f&&/^baseurl/{print $3; exit}' \
              /etc/yum.repos.d/redhat.repo)"
  [ -n "$base_tpl" ] || err "Could not read a baseurl for $baseos_id from redhat.repo"

  ent_curl() {  # entitlement-authenticated GET; prints HTTP code, 000 on failure
    curl -s -o /dev/null -w '%{http_code}' --cacert /etc/rhsm/ca/redhat-uep.pem \
         --cert "$cert" --key "$key" "$1" 2>/dev/null \
      || curl -s -o /dev/null -w '%{http_code}' --cert "$cert" --key "$key" "$1" 2>/dev/null \
      || echo 000
  }

  # Candidate minors: the CDN listing if reachable, else brute-force N.10..N.0.
  cands="$( { curl -sf --cacert /etc/rhsm/ca/redhat-uep.pem --cert "$cert" --key "$key" \
                "https://cdn.redhat.com/content/eus/rhel${major}/listing" \
           || curl -sf --cert "$cert" --key "$key" \
                "https://cdn.redhat.com/content/eus/rhel${major}/listing"; } 2>/dev/null \
           | grep -E '^[0-9]+\.[0-9]+$' | sort -rV || true)"
  [ -n "$cands" ] || cands="$(for i in $(seq 10 -1 0); do echo "${major}.${i}"; done)"

  latest=""; last_code=""
  for m in $cands; do
    url="${base_tpl//\$releasever/$m}"; url="${url//\$basearch/s390x}/repodata/repomd.xml"
    code="$(ent_curl "$url")"
    log "  probe $m → HTTP $code"
    if [ "$code" = "200" ]; then latest="$m"; break; fi
    last_code="$code"
  done

  if [ -n "$latest" ]; then
    log "Pinning releasever to $latest (verified against the CDN)"
    RELEASEVER_ARGS=(--releasever="$latest")
  elif [ "$last_code" = "403" ]; then
    err "The CDN rejected the entitlement client cert (HTTP 403 on every minor).
    This usually means a TLS-intercepting proxy is terminating the connection to
    cdn.redhat.com, which breaks certificate authentication — ask the network
    team to bypass (not intercept) cdn.redhat.com, or run the harvest from a
    host with direct egress."
  else
    err "No published minor found for the EUS stream (last HTTP code: ${last_code:-none}).
    Check connectivity to cdn.redhat.com from inside the container."
  fi
fi

log "Ensuring dnf-plugins-core (UBI repos)..."
# From the UBI subset only: the freshly enabled RHEL EUS repos would be
# metadata-refreshed too and 404 without the releasever pin.
dnf -y install --disablerepo='rhel-*' dnf-plugins-core

log "Ensuring createrepo_c (entitled RHEL repos)..."
# createrepo_c is NOT in the UBI repo subset — it has to come from the
# entitled RHEL repos, releasever-pinned when on the EUS stream.
dnf -y install --disablerepo='ubi-*' "${RELEASEVER_ARGS[@]}" createrepo_c

log "Downloading ${#PKG_ARR[@]} package(s) + full s390x dependency tree..."
# --disablerepo=ubi-*: resolve purely against the entitled RHEL s390x repos,
# not the UBI subset baked into the harvester image.
dnf download --resolve --alldeps --forcearch=s390x \
    --disablerepo='ubi-*' "${RELEASEVER_ARGS[@]}" --destdir=/out "${PKG_ARR[@]}"

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
  -e DIAGNOSE="$DIAGNOSE"
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
