# RHEL 10 bootc for s390x – Full Build, Deploy & Operations Guide

## Table of Contents
1. [Overview](#Overview)
2. [Assumptions & Requirements](#Assumptions&Requirements)
3. [Architecture Summary](#ArchitectureSummary)
4. [Base Image Acquisition](#BaseImageAcquisition)
5. [Creating the Containerfile](#CreatingtheContainerfile)
6. [Building the bootc Image](#BuildingthebootcImage)
7. [Testing the Container Image](#TestingtheContainerImage)
8. [Building s390x Bootable Disk Images](#Buildings390xBootableDiskImages)
9. [Deploying to IBM Z (LPAR or KVM)](#DeployingtoIBMZ(LPARorKVM))
10. [LPAR DASD/FCP Boot Requirements](#LPARDASD/FCPBootRequirements)
11. [First Boot Validation](#FirstBootValidation)
12. [Atomic Updates & Rollbacks](#AtomicUpdates&Rollbacks)
13. [Troubleshooting & Known Fixes](#Troubleshooting&KnownFixes)
14. [Full Reference Commands](#FullReferenceCommands)
15. [Appendix: Windows Build Flow](#Appendix:WindowsBuildFlow)

---
# Overview
This document explains how to build, deploy, and maintain RHEL 10 bootc container-based operating system images for IBM Z (s390x).

---
# Assumptions & Requirements
- podman
- bootc
- bootc-image-builder
- RHEL entitlement

---
# Base Image Acquisition
```bash
podman login registry.redhat.io
podman pull registry.redhat.io/rhel10/rhel-bootc:latest
```

---
# Creating the Containerfile
```Dockerfile
FROM registry.redhat.io/rhel10/rhel-bootc:latest

COPY rpms/ /tmp/rpms/
RUN rpm -Uvh /tmp/rpms/*.rpm || true

RUN dnf -y install \
    openssh-server \
    vim \
    curl \
    policycoreutils \
    chrony \
    rsyslog \
    s390utils-base \
    zipl \
    dracut \
    && dnf -y clean all

RUN systemctl enable sshd rsyslog chronyd
RUN useradd -m -G wheel britley && echo 'britley:changeme' | chpasswd
RUN sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
```

---
# Building the bootc Image
```bash
podman build -t quay.io/yourns/rhel10-bootc-s390x:base .
```

---
# Testing the Container
```bash
podman run --rm -it quay.io/yourns/rhel10-bootc-s390x:base /bin/bash
```

---
# Building s390x Bootable Disk Images
```bash
sudo dnf -y install bootc-image-builder
bootc-image-builder \
  --image quay.io/yourns/rhel10-bootc-s390x:base \
  --target s390x \
  --output /var/tmp/rhel10-bootc-s390x.raw
```

---
# Deploying to IBM Z (LPAR or KVM)
```bash
scp /var/tmp/rhel10-bootc-s390x.raw root@kvm-host:/var/lib/images/
```

---
# LPAR DASD/FCP Boot Requirements
```
rd.zfcp
rd.dasd
rd.net
```
```bash
zipl -v
```

---
# First Boot Validation
```bash
ssh britley@<ip>
uname -a
cat /etc/os-release
systemctl status sshd
```

---
# Atomic Updates & Rollbacks
```bash
sudo bootc upgrade
sudo bootc switch quay.io/yourns/rhel10-bootc-s390x:prod
sudo bootc rollback
sudo systemctl soft-reboot
```

---
# Troubleshooting & Fixes
```bash
sudo sh -c 'echo "sslverify=false" >> /etc/dnf/dnf.conf'
curl -k -o /etc/pki/ca-trust/source/anchors/redhat-uep.pem https://cdn.redhat.com/redhat-uep.pem
sudo update-ca-trust
sed -i 's/^sslverify = 1/sslverify = 0/g' /etc/yum.repos.d/redhat.repo
dnf --setopt=sslverify=false install fuse-overlayfs
```

---
# Windows Build Flow
```powershell
./Build-RHEL10-BootcDisk.ps1 -BootcImage images.pkgrepo.bcbssc.com/mu94/rhel10-bootc-s390x:base `
                             -OutputPath "C:\Users\YourUser\output" `
                             -ImageType qcow2
```
```powershell
docker run --rm -it --privileged --security-opt seccomp=unconfined `
  -v C:\Users\mu94\scripts\s390x-build\output:/output `
  registry.redhat.io/rhel10/bootc-image-builder:latest `
  --type qcow2 --target-arch s390x rhel10-new-s390x
```
```powershell
docker login images.pkgrepo.bcbssc.com
docker tag rhel10-new-s390x:stable images.pkgrepo.bcbssc.com/mu94/rhel10-new-s390x:stable
docker push images.pkgrepo.bcbssc.com/mu94/rhel10-new-s390x:stable
```
