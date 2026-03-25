# RHEL10-s390x
This is the instructions and scripts needed to build an s390x RHEL10 image from podman on x86 for IBM mainframe

# RHEL 10 bootc for IBM Z (s390x)

This repository builds a fully working RHEL 10 bootc-based operating system image
for IBM Z (s390x), suitable for LPAR, KVM, and ZD&T environments.

Components included:
- Complete dracut s390x driver set
- NetworkManager qeth configuration
- zipl bootloader configuration
- SSH hardened access with key injection
- Root filesystem mapping (fstab)
- SELinux permissive to avoid first-boot blocking

## Build

```bash
podman build -t rhel10-bootc-s390x:latest .
