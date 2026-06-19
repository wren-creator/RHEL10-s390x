# bootc Image Builder — Web UI How-To

`bootc-builder-server.py` is a self-contained Python web server that runs on your Linux build host. It serves a browser-based form for configuring a RHEL 10 bootc image, then either generates a ready-to-run shell script or executes the build directly on the host with live streaming output.

---

## Prerequisites

### On the build host

| Requirement | Notes |
|---|---|
| Python 3.6+ | Ships with RHEL 9/10 — no extra packages needed |
| `podman` | `dnf install -y podman` |
| RHEL subscription | `subscription-manager register` |
| `/etc/pki/entitlement` certs | Populated after registration |
| `registry.redhat.io` login | `podman login registry.redhat.io` |
| Root or passwordless sudo | Required to run the generated script |

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
Pre-flight checks → Configure form → Generate Script  →  copy & run manually
                                   → Build on This Host →  live output in browser
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
| Boot DASD address | CCW bus address of the DASD to boot from (e.g. `0.0.0200`). Written to `/etc/dasd.conf` and `zipl.conf`. |
| DD target DASD device | Block device path for the DASD (e.g. `/dev/dasda`). This is what the RAW image gets `dd`'d to. |
| Storage layout | **LVM** creates two logical volumes: `root` (40 GB) and `var` (20 GB) on first boot. **Single XFS** uses one partition with `LABEL=rootfs`. |
| LVM volume group name | Name of the VG created during first boot (default: `rhelvg`). Used in `fstab` and zipl kernel parameters. |

> **Warning:** The DD target DASD will be fully overwritten. The script prompts `Type YES to continue` before `dasdfmt` runs.

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

### ⚙ Build on This Host

Runs the build directly on the machine serving this page. Output streams live into the **Build Output** terminal panel in the browser.

- The build runs in the background — you can navigate away and return without losing output (as long as the server stays running)
- The button is disabled while a build is in progress
- Exit code is shown when the build completes (`✓ complete` or `✗ failed (rc=N)`)

> The "Build on This Host" button only makes sense when you are accessing the server from a machine that **is** the build host (i.e. `http://localhost:8080` or same machine). If you open the UI from a remote workstation, use Generate Script instead.

---

## What the generated script does

| Step | Action |
|---|---|
| 0 | Pre-flight: checks for podman, root, and target block device |
| 1 | `podman login registry.redhat.io` |
| 2 | Writes all build context files (dracut config, network config, fstab, authorized_keys, and optionally dasd.conf, zipl.conf, firstboot-lvm.sh) |
| 3 | Writes the Containerfile into the build context |
| 4 | `podman build --platform linux/<arch>` |
| 5 | `podman run bootc-image-builder --type <format> --target-arch <arch>` |
| 6–9 | **s390x RAW only:** DASD bring-online → dasdfmt → dd → zipl |
| 6 | **x86_64/aarch64 RAW only:** dd image to target disk |
| — | **Non-RAW:** prints output file location |

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
