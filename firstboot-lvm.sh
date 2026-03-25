#!/bin/bash
# /usr/local/sbin/firstboot-lvm.sh

# Bring DASDs online
for d in 0.0.0200 0.0.0300; do
    cio_ignore -r $d
    chccwdev -e $d
done

# Format DASDs if needed
dasdfmt -b 4096 -d cdl -p /dev/dasda

# Create LVM structure
pvcreate /dev/dasda
vgcreate rhelvg /dev/dasda
lvcreate -L 40G -n root rhelvg
mkfs.xfs /dev/rhelvg/root
