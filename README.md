# RHEL 10 · Image Mode Studio (s390x)

A web-driven studio that builds RHEL 10 image-mode OS images for IBM Z (s390x) and
hands you a **downloadable RAW disk image** ready to `dd` onto a DASD — supporting LPAR,
KVM, and ZD&T environments.

## The two-phase model

You cannot write to a DASD from a Windows/x86 build host, so the workflow splits cleanly:

- **Phase A — Build (any host).** The web app (`bootc-builder-server.py`) assembles a
  Containerfile from point-and-click choices, cross-compiles the s390x image under QEMU
  (auto-detecting **docker/buildx** or **podman**), runs `bootc-image-builder`, and
  produces a **RAW image you download from the browser**. On a native s390x host it skips
  emulation. This is where the app's job ends.
- **Phase B — Deploy (IBM Z host).** You take the downloaded RAW to a Linux-on-Z host
  with the DASD attached and run `dasdfmt` → `fdasd` → `dd` → `zipl`. The app never
  touches a physical DASD; the exact commands are generated for you (with your DASD
  addresses) in the script's `PHASE B` block and documented in the Deploy Guide.

```
Windows / x86 / z   →  build s390x image  →  RAW file  →  [ Download ]
                                                              │
IBM Z host (DASD)   ←──────── dasdfmt / fdasd / dd / zipl  ◄──┘   (Phase B, manual)
```

---

## Documentation

| Document | Purpose |
|---|---|
| [`Documentation/bootc-builder-server.md`](./Documentation/bootc-builder-server.md) | How-to for the Studio web app — engine prep, form fields, pre-flight, build & download |
| [`Documentation/Windows_Validation_Runbook.md`](./Documentation/Windows_Validation_Runbook.md) | **Phase A** validation on Windows / Docker Desktop — engine prep → cross-build → RAW → download, plus the entitlement strategy |
| [`Documentation/Deploy_Guide.md`](./Documentation/Deploy_Guide.md) | **Phase B** runbook — writing the RAW to DASD and IPL on IBM Z (Podman/LVM/RAW reference config) |
| [`RHEL10_bootc_s390x.md`](./RHEL10_bootc_s390x.md) | General reference — all environments, build options, VG/LVM setup, troubleshooting |

**Start with `bootc-builder-server.md`** to run the Studio and download your first RAW image (Phase A).  
**Use `Deploy_Guide.md`** for the DASD write and IPL once you have the image (Phase B).  
**Use `RHEL10_bootc_s390x.md`** as a reference for other environments (KVM, ZD&T) or manual builds.

---

## Repository Layout

```
.
├── containerfile                          # Containerfile for the bootc image
├── dracut/
│   └── 10-s390x.conf                     # s390x driver config for initramfs
├── network/
│   └── qeth0.nmconnection                # NetworkManager qeth profile (enc600, DHCP)
├── ssh/
│   └── authorized_keys                   # SSH public key for britley — REPLACE BEFORE BUILDING
├── zipl/
│   └── zipl.conf                         # IBM Z bootloader config
├── scripts/
│   ├── firstboot-lvm.sh                  # First-boot DASD/LVM provisioning script
│   ├── fetch-rpms.sh                     # Harvest s390x RPMs with your Red Hat account (no entitled host needed)
│   └── package-list.s390x.txt            # Package set for the image + harvester
├── systemd/
│   └── firstboot-lvm.service             # Systemd unit for first-boot LVM setup
├── dasd.conf                             # DASD device persistence config
├── fstab                                 # Root filesystem mount config
├── bootc-builder-server.py               # Image Mode Studio web app (Phase A)
├── studio.sh                             # Start/stop the Studio (macOS/Linux)
├── studio.ps1                            # Start/stop the Studio (Windows)
├── rpms/                                 # Drop local RPMs here (optional, can be empty)
├── rpm-cache/                            # Created by fetch-rpms.sh — local dnf repo, auto-used by builds (git-ignored)
├── RHEL10_bootc_s390x.md                 # General reference guide
├── Deploy_Guide.md                       # LPAR deployment runbook
└── README.md
```

---

## Quick Start — the Studio (Phase A)

Run the web app on your build host (Windows/Docker Desktop, x86 Linux, or native s390x).
One command to start it:

```bash
# macOS / Linux
./studio.sh start        # → prints the URL; stop | restart | status | logs
```

```powershell
# Windows
.\studio.ps1 start       # stop | restart | status | logs
```

<sub>Prefer to run it in the foreground? `python3 bootc-builder-server.py` works too.
Log in to the registry first: `podman login registry.redhat.io` (or `docker login …`).</sub>

In the browser:

1. **Run pre-flight checks** — confirms the container engine, host arch, and (for
   cross-builds) whether QEMU s390x emulation is registered.
2. **Prepare Build Engine** — one click self-heals the `binfmt`/`buildx` layer so an
   x86/ARM host can build s390x. Native s390x hosts skip this. On a network that
   TLS-intercepts through a corporate root CA, set `STUDIO_CA_CERT=/path/to/ca.pem`
   first — the docker buildx builder runs in its own isolated container and needs the
   CA trusted separately from the host (see the Windows runbook's troubleshooting table).
3. **Configure** — architecture, admin user + SSH key, DASD/qeth, storage, security.
4. **Build Image** — streams live logs; on success a **Download** button appears with the
   RAW image. (Or **Generate Script** to run the same build yourself.)

Then continue with **Phase B** ([`Deploy_Guide.md`](./Documentation/Deploy_Guide.md)) on the Z host.

> **Cross-build note:** on a non-RHEL host (e.g. Windows), host entitlement certs
> (`/etc/pki/entitlement`) don't exist, so they aren't mounted into the build. Layers that
> `dnf install` from the RHEL CDN need an entitlement strategy (a subscribed builder, or a
> registry pull secret). Packages already present in `rhel-bootc` build fine.
>
> **No entitled host? Harvest the RPMs once instead.** `./scripts/fetch-rpms.sh` registers
> a throwaway UBI container against **your own Red Hat account** (nothing touches the host),
> downloads the full s390x package set + dependency tree, and builds a local dnf repo at
> `rpm-cache/s390x/`. Every subsequent build auto-detects the cache and installs from it
> fully offline — no entitlement needed at build time. Non-interactive use: set `RH_ORG` +
> `RH_ACTIVATION_KEY`.

---

## Manual build (reference)

### Prerequisites

```bash
sudo dnf -y install podman qemu-user-static
sudo podman run --rm --privileged multiarch/qemu-user-static --reset -p yes
podman login registry.redhat.io
```

### Before your first build

1. **Replace the SSH key** — `ssh/authorized_keys` contains a placeholder. Add your real public key:
   ```bash
   cat ~/.ssh/id_ed25519.pub > ssh/authorized_keys
   ```

2. **Verify device addresses** — check that the DASD address and qeth channel in `dasd.conf`, `zipl/zipl.conf`, and `network/qeth0.nmconnection` match your actual LPAR configuration.

3. **Change the default password** — the containerfile sets `test:changeme`. Update this before building for any non-test environment.

### Build

Use the Studio's **Generate Script** button to produce a `build-and-deploy.sh`
tailored to your configuration, then run it:

```bash
chmod +x build-and-deploy.sh
sudo ./build-and-deploy.sh
```

Or manually:

```bash
podman build \
  --platform linux/s390x \
  --tls-verify=false \
  -t rhel10-bootc-s390x:latest \
  -f containerfile \
  .
```

### Create bootable disk image

```bash
podman run --rm -it --privileged \
  -v /var/lib/containers:/var/lib/containers \
  -v $(pwd)/output:/output \
  registry.redhat.io/rhel10/bootc-image-builder:latest \
  --type raw \
  --target-arch s390x \
  rhel10-bootc-s390x:latest
```

### Windows (Docker Desktop)

Run the **Studio** (`python bootc-builder-server.py`) — it drives Docker Desktop's buildx
engine for you (auto-detected), builds the s390x image under QEMU, and gives you the RAW to
download. See [Quick Start](#quick-start--the-studio-phase-a) above.

---

## Atomic Updates

Once deployed, OS updates are managed through `bootc` — not `dnf`:

```bash
sudo bootc upgrade       # pull latest image and stage for reboot
sudo bootc rollback      # revert to previous image
sudo systemctl soft-reboot
```
