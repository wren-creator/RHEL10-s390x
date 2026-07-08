# RHEL 10 bootc s390x — Deployment Guide (Phase B)

> **Scope:** This is **Phase B** of the [Image Mode Studio](../README.md) workflow — everything that happens *after* you download the RAW image from the Studio (Phase A). Run these steps on the IBM Z host with the DASD attached. It assumes an LPAR target with LVM on DASD and a RAW output format. The Studio also emits these exact commands (with your DASD addresses) in the generated script's `PHASE B` block.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Transfer the image to your IBM Z host](#2-transfer-the-image-to-your-ibm-z-host)
3. [Prepare the DASD device](#3-prepare-the-dasd-device)
4. [Write the image to DASD](#4-write-the-image-to-dasd)
5. [Install the zipl bootloader](#5-install-the-zipl-bootloader)
6. [IPL the system](#6-ipl-the-system)
7. [First-boot: LVM provisioning](#7-first-boot-lvm-provisioning)
8. [First-boot: user and SSH hardening](#8-first-boot-user-and-ssh-hardening)
9. [Validate the running system](#9-validate-the-running-system)
10. [Post-deployment hardening](#10-post-deployment-hardening)
11. [Atomic updates and rollback](#11-atomic-updates-and-rollback)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick-reference commands](#13-quick-reference-commands)

---

## 1. Prerequisites

### On the build host (Linux/Podman)
- RAW image produced by `build-and-deploy.sh` at `/var/tmp/bootc-output/*.raw`
- `ssh` / `scp` available for transfer

### On the IBM Z host (LPAR or KVM)
- Root or sudo access
- `s390-tools` package installed (`cio_ignore`, `chccwdev`, `lsdasd`, `dasdfmt`, `fdasd`, `zipl`)
- Target DASD device confirmed and addressable (default: `0.0.0200`)
- Network connectivity (qeth channel `0.0.0600`, interface `enc600`)

### Verify the image before transfer

```bash
# Confirm the file exists and is non-zero
ls -lh /var/tmp/bootc-output/*.raw

# Sanity check the image type
file /var/tmp/bootc-output/*.raw
```

---

## 2. Transfer the image to your IBM Z host

```bash
# Replace <ibmz-host> with the IP or hostname of your IBM Z system
scp /var/tmp/bootc-output/*.raw root@<ibmz-host>:/var/lib/images/

# Verify the transfer completed correctly (compare sizes)
ssh root@<ibmz-host> "ls -lh /var/lib/images/*.raw"
```

> **Tip:** For large images over slow links, use `rsync -avz --progress` instead of `scp` — it supports resuming interrupted transfers.

```bash
rsync -avz --progress /var/tmp/bootc-output/*.raw root@<ibmz-host>:/var/lib/images/
```

---

## 3. Prepare the DASD device

Run all commands in this section **on the IBM Z host** as root.

### 3.1 Remove DASD from the channel ignore list

```bash
cio_ignore -r 0.0.0200
```

> If this returns an error saying the device is not in the ignore list, that is fine — continue.

### 3.2 Bring the DASD online

```bash
chccwdev -e 0.0.0200
```

### 3.3 Confirm the device is visible to Linux

```bash
lsdasd
# Look for 0.0.0200 in the output with status "online"
```

Expected output (example):

```
Bus-ID     Status      Name      Device  Type  BlkSz  Size      Blocks
================================================================================
0.0.0200   active      dasda     94:0    ECKD  4096   10240MB   2621440
```

### 3.4 Low-level format the DASD

> **WARNING:** `dasdfmt` is **destructive**. It erases all existing data on the device. Confirm the device address is correct before running.

```bash
# CDL layout, 4096-byte blocks — correct for RHEL 10 on IBM Z
dasdfmt -b 4096 -d cdl -y /dev/dasda
```

The `-y` flag suppresses the confirmation prompt. Formatting a 10 GB DASD takes roughly 2–5 minutes.

### 3.5 Create a partition

DASD supports a maximum of **3 partitions**. For a single-root deployment, one partition is sufficient:

```bash
fdasd -a /dev/dasda
# -a creates a single partition spanning the entire device
```

Confirm the partition was created:

```bash
ls -l /dev/dasda*
# Expect: /dev/dasda  and  /dev/dasda1
```

---

## 4. Write the image to DASD

```bash
cd /var/lib/images

# Write the RAW image directly to the DASD block device
dd if=rhel10-bootc-s390x.raw of=/dev/dasda bs=64M status=progress

# Ensure all writes are flushed
sync
```

> The `dd` command writes the entire disk image — partition table, bootloader, filesystem, and all — directly to the device. Do **not** write to `/dev/dasda1` (the partition); write to `/dev/dasda` (the whole device).

---

## 5. Install the zipl bootloader

The RAW image includes a pre-configured `/etc/zipl.conf`. If the bootloader embedded correctly during the build, this step may be optional. It is always safe to run.

```bash
# Mount the root partition
mount /dev/dasda1 /mnt

# Install zipl
zipl --verbose --target /mnt

# Unmount
umount /mnt
```

Expected zipl output:

```
Preparing boot device for ECKD/CDL: dasda (0200).
Done.
```

---

## 6. IPL the system

### From the HMC

1. Open the **HMC** web console
2. Navigate to your LPAR
3. Select **Actions → Load**
4. Set **Load type** to `Normal`
5. Set **Load address** to `0200` (your DASD device address without the `0.0.` prefix)
6. Click **OK**

### From z/VM

```
IPL 0200
```

### From a Linux KVM host (if running as a guest)

```bash
virsh start <vm-name>
```

---

## 7. First-boot: LVM provisioning

The `firstboot-lvm.service` systemd unit runs automatically on the first boot. It performs the following actions in order:

| Step | Action |
|------|--------|
| 1 | `cio_ignore -r 0.0.0200` — remove DASD from ignore list |
| 2 | `chccwdev -e 0.0.0200` — bring DASD online |
| 3 | `dasdfmt` — low-level format (CDL, 4096-byte blocks) |
| 4 | `fdasd -a` — create single partition |
| 5 | `pvcreate` → `vgcreate rhelvg` → `lvcreate root (40G) + var (20G)` |
| 6 | `mkfs.xfs` on both logical volumes |
| 7 | Self-disable; write `/var/lib/firstboot-lvm.done` |

### Monitor first-boot progress via the console

Watch the console output during first boot. You will see log lines from `firstboot-lvm.sh` as each step completes.

### Verify after boot

```bash
# Check the log
cat /var/log/firstboot-lvm.log

# Confirm LVM structure
lvs
vgs

# Confirm filesystems are mounted
df -h
```

Expected `lvs` output:

```
  LV   VG      Attr       LSize
  root rhelvg  -wi-ao---- 40.00g
  var  rhelvg  -wi-ao---- 20.00g
```

---

## 8. First-boot: user and SSH hardening

### 8.1 Find the system IP address

If you have console access:

```bash
ip addr show enc600
# or
nmcli device show enc600 | grep IP4
```

### 8.2 First SSH login (password, forced change)

```bash
ssh britley@<ip-of-lpar>
```

britley's password is set to **expire immediately**. You will see:

```
WARNING: Your password has expired.
New password:
Retype new password:
```

Choose a strong password. You will then be logged in.

### 8.3 Verify key-based login works

From a **separate terminal** (keep your current session open as a fallback):

```bash
ssh -i ~/.ssh/britley_ibmz britley@<ip-of-lpar>
```

If this succeeds, you are ready to disable password authentication.

### 8.4 Disable password authentication

```bash
sudo sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl reload sshd

# Confirm
grep PasswordAuthentication /etc/ssh/sshd_config
# Expected: PasswordAuthentication no
```

> **Do not close your existing SSH session** until you have confirmed a new key-only session connects successfully.

---

## 9. Validate the running system

```bash
# Kernel and architecture
uname -a
# Expected: Linux <hostname> <version> ... s390x s390x s390x GNU/Linux

# OS release
cat /etc/os-release
# Expected: RHEL 10.x

# Bootc image status
sudo bootc status

# Services
systemctl status sshd NetworkManager chronyd rsyslog

# Network
nmcli device status
ip addr show enc600

# Storage
df -h
lvs
vgs
cat /etc/fstab

# SELinux
getenforce
# Will show: Permissive (expected at this stage)

# DASD persistence
cat /etc/dasd.conf
lsdasd
```

---

## 10. Post-deployment hardening

Work through this checklist before putting the system into production.

- [ ] Password changed from the temporary value on first login
- [ ] Key-based SSH login confirmed working: `ssh -i ~/.ssh/britley_ibmz britley@<ip>`
- [ ] Password authentication disabled in `/etc/ssh/sshd_config`
- [ ] First-boot LVM log shows success: `cat /var/log/firstboot-lvm.log`
- [ ] Disk layout confirmed: `df -h` and `lvs`
- [ ] Network confirmed: `nmcli device show enc600`
- [ ] `bootc status` confirms image is registered and pinned
- [ ] SELinux switched to enforcing once image is stable:

```bash
sudo sed -i 's/^SELINUX=permissive/SELINUX=enforcing/' /etc/selinux/config
# Then do a full IPL (not soft-reboot) to relabel the filesystem
```

- [ ] Rollback tested in a non-production LPAR before production deployment
- [ ] DASD device addresses documented alongside this guide

---

## 11. Atomic updates and rollback

RHEL image mode manages OS updates as container image swaps — no package-level patching.

### Pull and apply a new image

```bash
sudo bootc upgrade
sudo systemctl soft-reboot
```

`soft-reboot` avoids a full hardware POST cycle on IBM Z. Use a full IPL only when kernel parameters or hardware configuration changes.

### Switch to a different image tag

```bash
sudo bootc switch rhel10-bootc-s390x:prod
sudo systemctl soft-reboot
```

### Roll back to the previous image

```bash
sudo bootc rollback
sudo systemctl soft-reboot
```

### Check current and staged image

```bash
sudo bootc status
```

---

## 12. Troubleshooting

### DASD not visible after boot

```bash
# Check if device is in the ignore list
cio_ignore -l | grep 0200

# Remove and bring online manually
cio_ignore -r 0.0.0200
chccwdev -e 0.0.0200
lsdasd
```

### Network not coming up (enc600 missing)

```bash
# Check if qeth channel is online
ls /sys/bus/ccw/devices/0.0.0600/

# Bring channel online manually
echo 1 > /sys/bus/ccw/devices/0.0.0600/online

# Restart NetworkManager
systemctl restart NetworkManager
nmcli device status
```

### LVM volumes missing at boot

```bash
# Check first-boot log
cat /var/log/firstboot-lvm.log

# Manually activate the VG
vgchange -ay rhelvg
lvs

# Verify kernel parameters include rd.lvm.lv
cat /proc/cmdline
```

### SSL errors during dnf operations inside the container build

```bash
# Add sslverify=false to dnf.conf on the build host
sudo sh -c 'echo "sslverify=false" >> /etc/dnf/dnf.conf'

# Or fetch and trust the Red Hat UEP cert
curl -k -o /etc/pki/ca-trust/source/anchors/redhat-uep.pem \
    https://cdn.redhat.com/redhat-uep.pem
sudo update-ca-trust
```

### dracut missing s390x drivers

```bash
# Rebuild initramfs with explicit driver list
dracut -f --add-drivers 'dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp'

# Confirm drivers are present in the initramfs
lsinitrd /boot/initramfs-$(uname -r).img | grep dasd
```

### zipl fails after dd deploy

```bash
# Confirm the DASD device is fully online and the partition exists
lsdasd
ls /dev/dasda*

# Remount and reinstall zipl
mount /dev/dasda1 /mnt
zipl --verbose --target /mnt
umount /mnt
```

---

## 13. Quick-reference commands

| Command | Purpose |
|---------|---------|
| `cio_ignore -r 0.0.0200` | Remove DASD from ignore list |
| `chccwdev -e 0.0.0200` | Bring DASD online |
| `lsdasd` | List all DASD devices and status |
| `dasdfmt -b 4096 -d cdl -y /dev/dasda` | Low-level format DASD (destructive) |
| `fdasd -a /dev/dasda` | Create single partition on DASD |
| `dd if=image.raw of=/dev/dasda bs=64M status=progress` | Write RAW image to DASD |
| `sync` | Flush all write buffers |
| `zipl --verbose --target /mnt` | Install IBM Z bootloader |
| `lvs` / `vgs` | Show logical / volume groups |
| `df -h` | Show mounted filesystems |
| `cat /var/log/firstboot-lvm.log` | Check first-boot LVM setup log |
| `bootc status` | Show current and staged OS image |
| `bootc upgrade` | Pull new image and stage for reboot |
| `bootc rollback` | Roll back to previous image |
| `systemctl soft-reboot` | Fast reboot (skips IBM Z POST cycle) |
| `getenforce` | Show SELinux mode |
| `nmcli device show enc600` | Show qeth network interface details |
| `ip addr show enc600` | Show IP address of qeth interface |

---

*Generated from RHEL 10 bootc s390x build context — Podman/Linux · LPAR · DASD · LVM · RAW*
