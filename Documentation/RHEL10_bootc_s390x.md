# RHEL 10 bootc for IBM Z (s390x) – Build, Deploy & Operations Guide

## Table of Contents
1. [Overview](#1-overview)
2. [Requirements](#2-requirements)
3. [Architecture Summary](#3-architecture-summary)
4. [Base Image Acquisition](#4-base-image-acquisition)
5. [Containerfile](#5-containerfile)
6. [Dracut Configuration](#6-dracut-configuration)
7. [Network Configuration](#7-network-configuration)
8. [Storage Configuration](#8-storage-configuration)
   - [fstab](#fstab)
   - [DASD Persistence](#dasd-persistence)
   - [zipl Boot Configuration](#zipl-boot-configuration)
9. [Volume Group Creation (LVM on DASD)](#9-volume-group-creation-lvm-on-dasd)
10. [Building the Image](#10-building-the-image)
11. [Creating a Bootable Disk Image](#11-creating-a-bootable-disk-image)
    - [Using bootc-image-builder (Linux)](#using-bootc-image-builder-linux)
    - [Using Docker on Windows](#using-docker-on-windows)
12. [Deploying to IBM Z](#12-deploying-to-ibm-z)
13. [First Boot Validation](#13-first-boot-validation)
14. [Atomic Updates & Rollbacks](#14-atomic-updates--rollbacks)
15. [Cross-Architecture Builds (QEMU/binfmt)](#15-cross-architecture-builds-qemubinfmt)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Overview

This guide covers building, deploying, and maintaining RHEL 10 bootc container-based OS images for IBM Z (s390x), suitable for LPAR, KVM, and ZD&T environments.

RHEL image mode packages the entire OS — kernel, initramfs, bootloader, firmware, and configuration — as an OCI container image. This enables atomic updates, easy rollbacks, and reproducible deployments.

---

## 2. Requirements

| Component | Notes |
|---|---|
| RHEL 10 build host | x86_64 or s390x with Podman installed |
| Podman | For building and running containers |
| bootc | Installed on the build host |
| bootc-image-builder | Pulled as a container from Red Hat registry |
| Red Hat subscription | Required to pull base images and install packages |
| QEMU/binfmt | Required only for cross-architecture builds on x86_64 |

---

## 3. Architecture Summary

```
Containerfile
     │
     ▼
podman build  ──►  OCI container image (bootc)
                         │
                         ▼
             bootc-image-builder
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
           QCOW2                  RAW
              │                     │
         KVM/ZD&T                LPAR/DASD
```

---

## 4. Base Image Acquisition

Authenticate to the Red Hat registry, then pull the RHEL 10 bootc base image:

```bash
podman login registry.redhat.io
podman pull registry.redhat.io/rhel10/rhel-bootc:latest
```

The base image contains the kernel, initrd, and bootloader components needed for IBM Z.

---

## 5. Containerfile

```dockerfile
FROM registry.redhat.io/rhel10/rhel-bootc:latest

# Optional: inject local RPMs
COPY rpms/ /tmp/rpms/
RUN rpm -Uvh /tmp/rpms/*.rpm || true

# Install baseline OS components and s390x drivers
RUN dnf -y install \
      openssh-server \
      vim \
      curl \
      chrony \
      rsyslog \
      policycoreutils \
      s390utils-base \
      zipl \
      dracut \
      NetworkManager \
      qemu-guest-agent \
      lvm2 \
  && dnf -y clean all

# Enable critical services
RUN systemctl enable \
      sshd \
      rsyslog \
      chronyd \
      NetworkManager

# Create admin user
RUN useradd -m -G wheel britley \
  && echo 'britley:changeme' | chpasswd

# Harden SSH and inject authorized keys
RUN sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
COPY ssh/authorized_keys /home/britley/.ssh/authorized_keys
RUN chmod 700 /home/britley/.ssh \
  && chmod 600 /home/britley/.ssh/authorized_keys \
  && chown -R britley:britley /home/britley/.ssh

# Set SELinux permissive to avoid first-boot blocking
RUN sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config

# Copy s390x boot and network configs
COPY dracut/10-s390x.conf /etc/dracut.conf.d/
COPY fstab /etc/fstab
COPY network/qeth0.nmconnection /etc/NetworkManager/system-connections/qeth0.nmconnection
COPY dasd.conf /etc/dasd.conf

# First-boot LVM/DASD automation
COPY firstboot-lvm.sh /usr/local/sbin/
COPY firstboot-lvm.service /etc/systemd/system/
RUN chmod +x /usr/local/sbin/firstboot-lvm.sh \
  && systemctl enable firstboot-lvm.service

# Rebuild initramfs to include DASD drivers
RUN dracut -f

# Relabel SELinux after changes
RUN fixfiles -F relabel

# Required metadata for bootc-image-builder
RUN bootc install-to-filesystem --rootfs /
```

> **Note:** Change the default password `changeme` before production use, and replace the example SSH key in `ssh/authorized_keys` with your real public key.

---

## 6. Dracut Configuration

`dracut/10-s390x.conf` — ensures s390x storage and network drivers are bundled into the initramfs:

```
add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "
hostonly="no"
omit_drivers+=" floppy "
```

---

## 7. Network Configuration

`network/qeth0.nmconnection` — NetworkManager profile for the qeth Ethernet device:

```ini
[connection]
id=qeth0
type=ethernet
interface-name=enc600
autoconnect=true

[ipv4]
method=dhcp

[ipv6]
method=ignore
```

Adjust `interface-name` to match your actual qeth device (e.g., `enc100`, `enc900`).

---

## 8. Storage Configuration

### fstab

`fstab` — maps the root filesystem by label:

```
LABEL=rootfs / xfs defaults 0 0
```

If using LVM on DASD, replace with the LV path (see section 9):

```
/dev/rhelvg/root / xfs defaults 0 0
```

### DASD Persistence

`dasd.conf` — brings DASD devices online automatically at boot:

```
0.0.0200 1
```

Add one line per DASD device. The `1` enables the device at startup.

### zipl Boot Configuration

`zipl/zipl.conf` — IBM Z bootloader configuration:

```ini
[defaultboot]
default = linux

[linux]
target = /boot
kernel = /boot/vmlinuz
ramdisk = /boot/initramfs.img
parameters = "root=LABEL=rootfs rd.dasd=0.0.0200 rd.zfcp=0.0.4000,0x5005076305ffd123,0x4023 rd.net=qeth,0.0.0600,layer2=1"
```

If booting from an LVM root on DASD, update the parameters line:

```
parameters = "root=/dev/rhelvg/root rd.dasd=0.0.0200 rd.lvm.lv=rhelvg/root rd.net=qeth,0.0.0600,layer2=1"
```

---

## 9. Volume Group Creation (LVM on DASD)

This section covers setting up LVM on a DASD disk for use as the root or data volume. Run these steps on a live system or in a first-boot script.

### Step 1 — Bring the DASD online

DASDs are ignored by default and must be explicitly enabled:

```bash
# Remove from the ignore list
cio_ignore -r 0.0.4b2e

# Set the device online
chccwdev -e 0.0.4b2e

# Confirm it's visible
lsdasd
```

### Step 2 — Low-level format the DASD

Required once per drive before first use. **This erases all data on the device.**

```bash
dasdfmt -b 4096 -d cdl -p /dev/disk/by-path/ccw-0.0.4b2e
```

### Step 3 — Partition the DASD (optional)

DASD devices support a maximum of three partitions:

```bash
fdasd -a /dev/dasda
```

Use `fdasd` (not `fdisk`) for DASD partitioning.

### Step 4 — Create the LVM Physical Volume

```bash
pvcreate /dev/dasda
# or, if partitioned:
pvcreate /dev/dasda1
```

### Step 5 — Create the Volume Group

```bash
vgcreate rhelvg /dev/dasda
```

Multiple DASD devices can be added to a single VG:

```bash
vgextend rhelvg /dev/dasdb
```

### Step 6 — Create Logical Volumes

```bash
lvcreate -L 40G -n root rhelvg
lvcreate -L 50G -n var  rhelvg
```

### Step 7 — Create Filesystems

bootc images use XFS by default:

```bash
mkfs.xfs /dev/rhelvg/root
mkfs.xfs /dev/rhelvg/var
```

### Step 8 — Update fstab

```
/dev/rhelvg/root   /      xfs  defaults 0 0
/dev/rhelvg/var    /var   xfs  defaults 0 0
```

### Step 9 — Persist DASD across reboots

Add entries to `/etc/dasd.conf`:

```
0.0.4b2e 1
0.0.2000 1
```

Then rebuild the initramfs so the devices are available at early boot:

```bash
dracut -f
```

### Step 10 — Kernel boot parameters for LVM on DASD

Ensure your zipl parameters include both the DASD device and the LVM volume:

```
rd.dasd=0.0.4b2e rd.lvm.lv=rhelvg/root
```

And your dracut config must include these drivers (already covered in section 6):

```
add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "
```

### First-boot Automation Script

`firstboot-lvm.sh` handles this automatically on first boot when deployed to a fresh DASD:

```bash
#!/bin/bash
# /usr/local/sbin/firstboot-lvm.sh

# Bring DASDs online
for d in 0.0.0200 0.0.0300; do
    cio_ignore -r $d
    chccwdev -e $d
done

# Format DASD
dasdfmt -b 4096 -d cdl -p /dev/dasda

# Create LVM structure
pvcreate /dev/dasda
vgcreate rhelvg /dev/dasda
lvcreate -L 40G -n root rhelvg
mkfs.xfs /dev/rhelvg/root
```

> **Note:** Adjust DASD device numbers and LV sizes to match your environment before deploying.

---

## 10. Building the Image

Build the container image using Podman. If building on x86_64 for s390x, see section 15 first.

```bash
podman build \
  --platform linux/s390x \
  --tls-verify=false \
  -t images.pkgrepo.example.com/rhel10-bootc-s390x:latest \
  -f containerfile \
  .
```

To mount Red Hat entitlements into the build context (required for `dnf install`):

```bash
podman build \
  --network=host \
  --platform linux/s390x \
  --tls-verify=false \
  -t images.pkgrepo.example.com/rhel10-bootc-s390x:latest \
  -f containerfile \
  --volume /etc/pki/entitlement:/etc/pki/entitlement:ro \
  --volume /etc/rhsm:/etc/rhsm:ro \
  --volume /etc/yum.repos.d/redhat.repo:/etc/yum.repos.d/redhat.repo:ro \
  .
```

Test the container interactively before converting to a disk image:

```bash
podman run --rm -it images.pkgrepo.example.com/rhel10-bootc-s390x:latest /bin/bash
```

---

## 11. Creating a Bootable Disk Image

### Using bootc-image-builder (Linux)

Pull the official builder image (requires Red Hat login):

```bash
podman login registry.redhat.io
podman pull registry.redhat.io/rhel10/bootc-image-builder:latest
```

Run it to produce a QCOW2 image:

```bash
podman run --rm -it --privileged \
  -v /var/lib/containers:/var/lib/containers \
  -v $(pwd)/output:/output \
  registry.redhat.io/rhel10/bootc-image-builder:latest \
  --type qcow2 \
  --target-arch s390x \
  images.pkgrepo.example.com/rhel10-bootc-s390x:latest
```

Supported output types: `qcow2`, `raw`, `iso`, `ami`, `vmdk`

> **Note:** `bootc-image-builder` is distributed as a container only — there is no RPM. Your base bootc image must already exist locally or be accessible from the registry.

### Using Docker on Windows

Use the provided PowerShell script `image-builder.ps1`:

```powershell
.\image-builder.ps1 `
  -BootcImage images.pkgrepo.example.com/rhel10-bootc-s390x:latest `
  -OutputPath "C:\Users\YourUser\output" `
  -ImageType qcow2
```

Or run the builder container directly:

```powershell
docker run --rm -it --privileged --security-opt seccomp=unconfined `
  -v C:\Users\YourUser\output:/output `
  registry.redhat.io/rhel10/bootc-image-builder:latest `
  --type qcow2 --target-arch s390x `
  images.pkgrepo.example.com/rhel10-bootc-s390x:latest
```

To tag and push the image to your registry:

```powershell
docker login images.pkgrepo.example.com
docker tag rhel10-bootc-s390x:latest images.pkgrepo.example.com/rhel10-bootc-s390x:stable
docker push images.pkgrepo.example.com/rhel10-bootc-s390x:stable
```

---

## 12. Deploying to IBM Z

**For KVM:**

Copy the QCOW2 image to the KVM host and define a VM using it:

```bash
scp output/image.qcow2 root@kvm-host:/var/lib/libvirt/images/rhel10-s390x.qcow2
```

**For LPAR (DASD):**

Copy the RAW image to the target system, then write it to the DASD device:

```bash
scp output/image.raw root@lpar-host:/tmp/

# On the LPAR host — ensure the target DASD is NOT mounted
dd if=/tmp/image.raw of=/dev/dasda bs=64M status=progress
sync
```

Reinstall the zIPL bootloader if needed:

```bash
mount /dev/dasda1 /mnt
zipl --verbose --target /mnt
umount /mnt
```

Then IPL the DASD from HMC or z/VM:

```
IPL <device_number>
```

---

## 13. First Boot Validation

```bash
ssh britley@<ip-address>
uname -a
cat /etc/os-release
systemctl status sshd
```

---

## 14. Atomic Updates & Rollbacks

Image mode uses `bootc` for OS updates instead of `dnf`. Changes are staged and applied atomically on reboot.

```bash
# Pull and stage the latest image, then reboot into it
sudo bootc upgrade

# Switch to a different image tag (e.g., prod)
sudo bootc switch images.pkgrepo.example.com/rhel10-bootc-s390x:prod

# Roll back to the previous image
sudo bootc rollback

# Soft reboot — avoids full POST cycle on IBM Z
sudo systemctl soft-reboot
```

---

## 15. Cross-Architecture Builds (QEMU/binfmt)

To build s390x images on an x86_64 host, enable QEMU user-mode emulation first:

```bash
sudo podman run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

Then proceed with `podman build --platform linux/s390x` as shown in section 10.

---

## 16. Troubleshooting

### SSL / certificate errors during dnf install

```bash
sudo sh -c 'echo "sslverify=false" >> /etc/dnf/dnf.conf'
sed -i 's/^sslverify = 1/sslverify = 0/g' /etc/yum.repos.d/redhat.repo

curl -k -o /etc/pki/ca-trust/source/anchors/redhat-uep.pem \
  https://cdn.redhat.com/redhat-uep.pem
sudo update-ca-trust
```

### fuse-overlayfs missing

```bash
dnf --setopt=sslverify=false install fuse-overlayfs
```

### Podman firewall issues during build

```bash
sudo mkdir -p /etc/containers/containers.conf.d
printf "[network]\nfirewall_driver=\"none\"\n" | \
  sudo tee /etc/containers/containers.conf.d/90-firewall-none.conf
```

### Subscription manager behind a proxy

Edit `/etc/rhsm/rhsm.conf` and set:

```
proxy = http://<proxy-host>:<port>
```

Then refresh:

```bash
sudo subscription-manager refresh
```

### NIC or DASD drivers missing after boot

Verify your dracut config (section 6) includes the required drivers and rebuild the initramfs:

```bash
dracut -f
```

Then confirm drivers are present:

```bash
lsinitrd /boot/initramfs-$(uname -r).img | grep -E 'dasd|qeth|zfcp'
```
