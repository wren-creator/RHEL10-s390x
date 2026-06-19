# RHEL 10 bootc for IBM Z (s390x)

Build and deploy RHEL 10 image-mode OS images for IBM Z (s390x) — supporting LPAR, KVM, and ZD&T environments.

---

## Documentation

| Document | Purpose |
|---|---|
| [`Documentation/bootc-builder-server.md`](./Documentation/bootc-builder-server.md) | How-to guide for the web UI builder — prerequisites, form fields, pre-flight checks, generate vs build, troubleshooting |
| [`Documentation/Deploy_Guide.md`](./Documentation/Deploy_Guide.md) | LPAR-specific deployment runbook — locked to this project's configuration (Podman/Linux, DASD, LVM, RAW image, britley user) |
| [`RHEL10_bootc_s390x.md`](./RHEL10_bootc_s390x.md) | General reference guide — covers all environments, build options, VG/LVM setup, troubleshooting |

**Start with `bootc-builder-server.md`** to get the web UI running and generate your first build script.  
**Use `Deploy_Guide.md`** for the full LPAR deployment runbook after you have a RAW image.  
**Use `RHEL10_bootc_s390x.md`** as a reference for anything not covered in the runbooks, or when adapting for a different environment (KVM, ZD&T, Windows build host, etc.).

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
│   └── build-and-deploy.sh               # End-to-end build automation script
├── systemd/
│   └── firstboot-lvm.service             # Systemd unit for first-boot LVM setup
├── dasd.conf                             # DASD device persistence config
├── fstab                                 # Root filesystem mount config
├── image-builder.ps1                     # Windows (Docker Desktop) build script
├── rpms/                                 # Drop local RPMs here (optional, can be empty)
├── RHEL10_bootc_s390x.md                 # General reference guide
├── Deploy_Guide.md                       # LPAR deployment runbook
└── README.md
```

---

## Quick Start

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

3. **Change the default password** — the containerfile sets `britley:changeme`. Update this before building for any non-test environment.

### Build

```bash
chmod +x scripts/build-and-deploy.sh
sudo ./scripts/build-and-deploy.sh
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

```powershell
.\image-builder.ps1 `
  -BootcImage rhel10-bootc-s390x:latest `
  -OutputPath "C:\Users\YourUser\output" `
  -ImageType qcow2
```

---

## Atomic Updates

Once deployed, OS updates are managed through `bootc` — not `dnf`:

```bash
sudo bootc upgrade       # pull latest image and stage for reboot
sudo bootc rollback      # revert to previous image
sudo systemctl soft-reboot
```
