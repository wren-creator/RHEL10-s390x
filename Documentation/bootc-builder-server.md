# bootc Image Builder — Web UI How-To

`bootc-builder-server.py` is a self-contained Python web server — the **Image Mode Studio**. It runs on any build host (Windows/Docker Desktop, x86 Linux, or native s390x), serves a browser-based form for configuring a RHEL 10 bootc image, cross-compiles the s390x image under QEMU (auto-detecting **docker/buildx** or **podman**), and produces a **RAW image you download from the browser**.

This is **Phase A** of the two-phase workflow described in the [README](../README.md): the app builds and hands you the image file. Writing that image to a DASD (`dasdfmt`/`fdasd`/`dd`/`zipl`) is **Phase B**, run manually on the IBM Z host — see [`Deploy_Guide.md`](./Deploy_Guide.md). The app never writes to a physical DASD.

---

## Prerequisites

### On the build host

| Requirement | Notes |
|---|---|
| Python 3.6+ | Ships with RHEL 9/10 — no extra packages needed |
| Container engine | `docker` **or** `podman` — auto-detected |
| RHEL subscription | Native/RHEL host: `subscription-manager register`. Cross-build host: a registry pull secret (see note below) |
| `/etc/pki/entitlement` certs | Native/RHEL host only — mounted into the build. Absent on cross-build hosts |
| `registry.redhat.io` login | `podman login registry.redhat.io` (or `docker login …`) |
| Root or passwordless sudo | Required to run the generated script and prepare the engine |

> **Cross-build entitlements:** on a non-RHEL host, host entitlement certs don't exist and aren't mounted. Layers that `dnf install` from the RHEL CDN then need a subscribed builder or a registry pull secret; packages already in `rhel-bootc` build fine.

For s390x cross-builds from an x86_64 host, also install:

```bash
dnf install -y qemu-user-static
podman run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

### From your workstation

Any modern browser on the same network as the build host.

---

## Starting the server

```bash
# Run as root (or with sudo) so the generated script can be executed in-browser
sudo python3 bootc-builder-server.py
```

Default port is **8080**. Open in a browser:

```
http://<build-host-ip>:8080
```

The server keeps running until you `Ctrl+C` it. It handles multiple browser connections concurrently.

---

## Workflow overview

```
Pre-flight → Prepare Build Engine → Configure → Generate Script → copy & run manually
                                              → Build Image      → live logs → Download RAW
```

---

## Step 1 — Pre-flight checks

Click **[ run checks ]** before doing anything else. The panel runs five checks and reports pass/fail inline:

| Check | What it tests |
|---|---|
| podman installed | `which podman` |
| RHEL entitlement certs | `/etc/pki/entitlement` has at least one `.pem` |
| /etc/rhsm config | `/etc/rhsm/rhsm.conf` exists |
| redhat.repo | `/etc/yum.repos.d/redhat.repo` exists |
| registry.redhat.io reachable | TCP connect to port 443 |
| registry.redhat.io login | `podman login --get-login` |

Fix any failures before proceeding. A failed registry login is the most common blocker — run `podman login registry.redhat.io` on the build host and re-check.

---

## Step 2 — Configure the form

### Section 00 · Build Target

**Architecture** — click one card:

| Card | Generates for |
|---|---|
| IBM Z — s390x | LPAR, z/VM, ZD&T, KVM on IBM Z |
| x86_64 | PC, VMware, KVM, cloud (AWS, Azure, GCP) |
| aarch64 | ARM64 cloud (AWS Graviton, Azure Ampere) |

Selecting s390x shows the DASD and qeth sections. Selecting x86_64 or aarch64 shows the generic storage and network sections instead.

**Output format** — click one pill:

| Format | Use case |
|---|---|
| RAW | Write directly to a block device (`dd`) |
| QCOW2 | KVM / ZD&T / libvirt |
| VMDK | VMware ESXi / Workstation |
| ISO | Bootable installer (bare-metal, USB) |

> Non-RAW formats produce an image file only — the generated script will print its location but will not write to any disk.

---

### Section 01 · Admin Identity

| Field | Description |
|---|---|
| Admin username | A new user created in the `wheel` group. This is your primary login account — root login is disabled over SSH. |
| SSH public key | Your full `ssh-ed25519 AAAA...` or `ssh-rsa AAAA...` public key. Written to `~/.ssh/authorized_keys` at build time. |

The admin password is set to `Ch@ngeMe1st!` and expires on first login. You will be forced to change it before getting a shell prompt.

---

### Section 02 · Storage

#### IBM Z — DASD Storage

| Field | Description |
|---|---|
| Boot DASD address | CCW bus address of the DASD to boot from (e.g. `0.0.0200`). Written to `/etc/dasd.conf`, `zipl.conf`, and the Phase B deploy snippet. |
| DD target DASD device | Block device path for the DASD (e.g. `/dev/dasda`). Used in the **Phase B** snippet — where you'll `dd` the RAW on the Z host. Nothing is written to a DASD on the build host. |
| Storage layout | **LVM** creates two logical volumes: `root` (40 GB) and `var` (20 GB) on first boot. **Single XFS** uses one partition with `LABEL=rootfs`. |
| LVM volume group name | Name of the VG created during first boot (default: `rhelvg`). Used in `fstab` and zipl kernel parameters. |

> **Warning:** These addresses configure the image and the **Phase B** snippet only — the build host writes nothing to a DASD. On the Z host in Phase B, `dasdfmt` fully erases the target DASD.

#### x86_64 / aarch64 — Storage

| Field | Description |
|---|---|
| Target block device | Block device path to `dd` the RAW image to (e.g. `/dev/sda`). Only relevant for RAW output. |
| Storage layout | Single XFS root only in Phase 1. LVM on a second disk is planned for a future release. |

---

### Section 03 · Network

#### IBM Z — qeth Network

| Field | Description |
|---|---|
| qeth base channel | Base CCW channel address (e.g. `0.0.0600`). Channels N, N+1, N+2 (0600, 0601, 0602) are used automatically. |
| Interface name | Linux NM interface name (e.g. `enc600`). Must match what the kernel assigns to this qeth device. |
| IP configuration | **DHCP** or **Static** (static means DHCP placeholder is written — edit the nmconnection after generation if needed). |

#### x86_64 / aarch64 — Network

| Field | Description |
|---|---|
| Network interface name | NM interface name (e.g. `eth0`, `ens3`, `enp0s3`). |
| IP configuration | DHCP or Static. |

---

### Section 04 · Build Options

| Field | Description |
|---|---|
| Output image name | Local container tag used during build (e.g. `rhel10-bootc-s390x`). Not pushed to any registry. |
| Image tag | Tag applied to the local image (e.g. `latest`, `v1.0`). |
| Output directory | Where `bootc-image-builder` writes the finished image file (default: `/var/tmp/bootc-output`). |
| HTTP proxy | Leave blank if not needed. Sets `http_proxy` / `https_proxy` / `no_proxy` in the script. |

---

### Section 05 · Security

| Field | Description |
|---|---|
| SELinux mode | Written to `/etc/selinux/config`. Use **Permissive** for initial deployment and switch to **Enforcing** once the system is validated. |
| FIPS 140-2 | Adds `fips=1` to kernel parameters, installs `crypto-policies-scripts`, adds the `fips` dracut module, and runs `update-crypto-policies --set FIPS` at build time. Requires a full reboot after first boot to activate. |

---

## Step 3 — Generate or Build

### ▶ Generate Script

Submits the form and returns a `bash` script in the output panel below the form. The script is complete and self-contained — copy it to your build host and run as root.

Use **[ copy ]** to copy the script to the clipboard, then:

```bash
# On the build host
cat > build-and-deploy.sh   # paste, Ctrl+D
chmod +x build-and-deploy.sh
sudo ./build-and-deploy.sh
```

### ⚙ Build Image

Runs the build on the machine serving this page. Output streams live into the **Build
Output — Phase A** terminal panel. On success, a **Download** button appears with the RAW
image; each build's artifacts are isolated per job so downloads never collide.

- The build runs in the background — you can navigate away and return without losing output (as long as the server stays running)
- The button is disabled while a build is in progress
- Exit code is shown when the build completes (`✓ complete` or `✗ failed (rc=N)`)
- The download streams the image in 1 MiB chunks, so multi-GB RAW files transfer fine

Once downloaded, continue with **Phase B** ([`Deploy_Guide.md`](./Deploy_Guide.md)) on the Z host.

### Prepare Build Engine

Before building on a non-s390x host, click **Prepare Build Engine**. It self-heals the QEMU
emulation layer (idempotent — re-running when already set up makes no changes):

- **docker:** ensures an isolated `docker-container` buildx builder registers `linux/s390x` (installs `tonistiigi/binfmt` if needed)
- **podman:** registers `multiarch/qemu-user-static` binfmt handlers if `qemu-s390x` isn't present
- **native s390x:** no-op — emulation isn't needed

---

## What the generated script does

| Step | Action |
|---|---|
| 0 | Pre-flight: checks the detected engine (`$ENGINE`), root, and build mode (native/cross) |
| 1 | `$ENGINE login registry.redhat.io` |
| 2 | Writes all build context files (dracut config, network config, fstab, authorized_keys, and optionally dasd.conf, zipl.conf, firstboot-lvm.sh) |
| 3 | Writes the Containerfile into the build context |
| 4 | Builds the image — `docker buildx build --platform linux/<arch> --load` or `podman build --platform linux/<arch>` (entitlement certs mounted only in native/RHEL mode) |
| 5 | `$ENGINE run bootc-image-builder --type <format> --target-arch <arch>` |
| 6 | Prints the output image path + `ARTIFACT_PATH=…` (used by the Download button) |
| — | Appends a **PHASE B** comment block (dasdfmt → fdasd → dd → zipl) for the Z host — **not executed here** |

---

## Troubleshooting

**Pre-flight shows registry login failed**
```bash
# On the build host
podman login registry.redhat.io
# Enter your Red Hat credentials (Customer Portal username/password)
```

**Pre-flight shows missing entitlement certs**
```bash
subscription-manager register --username <rhel-user> --password <rhel-pass>
subscription-manager attach --auto
subscription-manager refresh
```

**Build fails: `cannot find -lseccomp` or similar**
The build runs inside a privileged container — ensure your build host has `libseccomp-devel` installed if needed, or check that `--security-opt seccomp=unconfined` is being passed (it is, by default).

**s390x DASD not appearing after `chccwdev -e`**
```bash
cio_ignore -l | grep <address>   # check if device is in ignore list
cio_ignore -r <address>          # remove from ignore list
chccwdev -e <address>
lsdasd                           # confirm status
```

**Network not coming up after boot (s390x)**
```bash
# Check if the qeth channel is online
ls /sys/bus/ccw/devices/0.0.0600/
echo 1 > /sys/bus/ccw/devices/0.0.0600/online
systemctl restart NetworkManager
```

**Port 8080 already in use**
Edit `PORT = 8080` at the top of `bootc-builder-server.py` and restart.

---

## Security notes

- The server binds to all interfaces (`0.0.0.0:8080`) — it is intended for use on a trusted internal network only. Do not expose it to the internet.
- The generated script embeds your SSH public key in plaintext — treat it like any other script with credentials.
- The default admin password `Ch@ngeMe1st!` appears in the generated Containerfile. Change it before using the image in production, or modify the `chpasswd` line in the generated script.
