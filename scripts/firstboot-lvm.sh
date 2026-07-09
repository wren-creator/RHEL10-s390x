#!/bin/bash
# /usr/local/sbin/firstboot-lvm.sh — first-boot DASD + LVM provisioning.
# Matches the version the Studio (bootc-builder-server.py) generates; edit the
# three vars below to your environment before a manual build.
set -euo pipefail
LOG=/var/log/firstboot-lvm.log
exec >> "$LOG" 2>&1
echo "=== firstboot-lvm started: $(date) ==="

DASD_ADDR="0.0.0200"
DASD_DEV="/dev/dasda"
VG_NAME="rhelvg"

echo "[1/6] Bringing DASD online..."
cio_ignore -r "$DASD_ADDR" || true
chccwdev -e "$DASD_ADDR"
for i in $(seq 1 20); do [ -b "$DASD_DEV" ] && break; sleep 1; done
[ -b "$DASD_DEV" ] || { echo "ERROR: $DASD_DEV not found"; exit 1; }

echo "[2/6] Low-level formatting..."
dasdfmt -b 4096 -d cdl -y "$DASD_DEV"

echo "[3/6] Partitioning..."
fdasd -a "$DASD_DEV"
PART="${DASD_DEV}1"
for i in $(seq 1 10); do [ -b "$PART" ] && break; sleep 1; done

echo "[4/6] Creating LVM PV, VG, LVs..."
pvcreate "$PART"
vgcreate "$VG_NAME" "$PART"
lvcreate -L 40G -n root "$VG_NAME"
lvcreate -L 20G -n var  "$VG_NAME"

echo "[5/6] Formatting XFS..."
mkfs.xfs -f "/dev/$VG_NAME/root"
mkfs.xfs -f "/dev/$VG_NAME/var"

echo "[6/6] Disabling service..."
systemctl disable firstboot-lvm.service
touch /var/lib/firstboot-lvm.done
echo "=== firstboot-lvm complete: $(date) ==="
