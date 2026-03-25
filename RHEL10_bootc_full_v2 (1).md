# RHEL10 bootc for s390x – Full Build & Deployment Guide (v2)

## Overview
This document provides a comprehensive guide for building, configuring, and deploying Red Hat Enterprise Linux 10 (RHEL10) bootc-based images for IBM Z (s390x) systems, including LPAR, KVM, and ZD&T environments.

## Prerequisites
- RHEL10 host with Podman Desktop
- bootc and bootc-image-builder installed
- Red Hat account and registry access
- QEMU/binfmt for cross-architecture builds

## Architecture Summary
RHEL image mode allows building OS images as OCI containers that include the kernel, initramfs, bootloader, firmware, and system configuration.

## Base Image Acquisition
```bash
podman login registry.redhat.io
podman pull registry.redhat.io/rhel10/rhel-bootc:latest
```

## Containerfile (Full Production Version)
```Dockerfile
FROM registry.redhat.io/rhel10/rhel-bootc:latest
COPY rpms/ /tmp/rpms/
RUN rpm -Uvh /tmp/rpms/*.rpm || true
RUN dnf -y install \
      openssh-server vim curl chrony rsyslog policycoreutils \
      s390utils-base zipl dracut NetworkManager qemu-guest-agent \
    && dnf -y clean all
RUN systemctl enable sshd rsyslog chronyd NetworkManager
RUN useradd -m -G wheel britley && echo 'britley:changeme' | chpasswd
RUN sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
COPY ssh/authorized_keys /home/britley/.ssh/authorized_keys
RUN chmod 700 /home/britley/.ssh && chmod 600 /home/britley/.ssh/authorized_keys && chown -R britley:britley /home/britley/.ssh
RUN sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
COPY dracut/10-s390x.conf /etc/dracut.conf.d/
COPY fstab /etc/fstab
COPY network/qeth0.nmconnection /etc/NetworkManager/system-connections/
RUN fixfiles -F relabel
RUN bootc install-to-filesystem --rootfs /
```

## Dracut Configuration
```
add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "
hostonly="no"
omit_drivers+=" floppy "
```

## Network Configuration (qeth)
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

## fstab
```
LABEL=rootfs / xfs defaults 0 0
```

## zipl.conf
```
[defaultboot]
default = linux
[linux]
target = /boot
kernel = /boot/vmlinuz
ramdisk = /boot/initramfs.img
parameters = "root=LABEL=rootfs rd.dasd=0.0.0200 rd.zfcp=0.0.4000,0x5005076305ffd123,0x4023 rd.net=qeth,0.0.0600,layer2=1"
```

## Building the Image
```bash
podman build -t rhel10-bootc-s390x:latest .
```

## Creating Bootable Disk
```bash
bootc-image-builder \
  --image rhel10-bootc-s390x:latest \
  --target s390x \
  --output /var/tmp/rhel10-bootc-s390x.raw
```

## Deploying to IBM Z
```bash
scp /var/tmp/rhel10-bootc-s390x.raw root@kvm-host:/var/lib/images/
```

## First Boot Validation
```bash
ssh britley@<ip>
uname -a
cat /etc/os-release
systemctl status sshd
```

## Atomic Updates
```bash
sudo bootc upgrade
sudo bootc rollback
sudo systemctl soft-reboot
```

## Cross-Architecture Notes (QEMU/Binfmt)
```bash
sudo podman run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

## Optional cloud-init Support
Install cloud-init for environments requiring dynamic provisioning.

## Multi-Stage Build Example
```Dockerfile
FROM registry.redhat.io/rhel10/rhel-bootc as base
RUN dnf -y install httpd && systemctl enable httpd
FROM base
COPY index.html /usr/share/www/html/index.html
```

## Advanced Partitioning
Use bootc-image-builder config to define partition layouts.

## Appendix: Windows Build Flow
```powershell
./Build-RHEL10-BootcDisk.ps1 -BootcImage rhel10-bootc-s390x:latest -OutputPath C:\output -ImageType qcow2
```
