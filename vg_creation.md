## 1. Bring the DASD disk online (LPAR or z/VM)
Before you can use a DASD disk with LVM, it must be visible to Linux and set online.
## Step 1: Remove from cio_ignore
Red Hat & IBM docs state you must remove ignored DASD devices first:
```
cio_ignore -r <device_number>
```
Example:
```
cio_ignore -r 4b2e
```
## Step 2: Set the DASD “online”
```
chccwdev -e <device_number>
```
Example:
```
chccwdev -e 4b2e
```
## Step 3: Confirm visibility
```
lsdasd
```
## 2. Low‑level format the DASD (required ONCE per drive)
Red Hat explicitly says DASDs must be low‑level formatted before first use:
```
dasdfmt -b 4096 -d cdl -p /dev/disk/by-path/ccw-0.0.<device_number>
```
Important: DASD formatting erases all content.
## 3. Create DASD partitions (if using partitions)
Remember: a DASD can only have three partitions maximum
(Red Hat + IBM limitation).
```
fdisk /dev/dasda
```
## 4. Create LVM Physical Volume (PV)
Once your DASD is online and formatted, convert it into a PV:
```
pvcreate /dev/dasda
```
(or /dev/dasda1 if you partitioned)
This matches standard LVM instructions from Red Hat:

## 5. Create the Volume Group (VG)
Example VG named rhelvg:
```
vgcreate rhelvg /dev/dasda
```
(You can add multiple DASDs into one VG.)
Red Hat LVM docs describe VGs as pools of PVs used to create LVs.

## 6. Create Logical Volumes (LV)
Example: create a root LV and a var LV:
```
lvcreate -L 20G -n root rhelvg
lvcreate -L 50G -n var rhelvg
```
## 7. Make Filesystems
Example (bootc images normally use XFS):
```
mkfs.xfs /dev/rhelvg/root
mkfs.xfs /dev/rhelvg/var
```
 ## 8. Mount and update /etc/fstab
Example fstab entries:
```
/dev/rhelvg/root   /      xfs  defaults 0 0
/dev/rhelvg/var    /var   xfs  defaults 0 0
```
## 9. Persist DASDs across reboot
Very important: DASDs will not stay online automatically.
Use /etc/dasd.conf:
```
0.0.4b2e 1
0.0.2000 1
```
Then rebuild initramfs:
```
dracut -f
```
## 10. Special notes for bootc-based (image mode) RHEL 10 images
If your root filesystem is inside an LVM VG on DASD, ensure your kernel parameters include:
```
rd.dasd=0.0.4b2e rd.lvm.lv=rhelvg/root
```
AND ensure these modules are in the initramfs (dracut config):
```
add_drivers+=" dasd_mod dasd_eckd_mod qdio qeth qeth_l2 zfcp "
```
## Complete configuration for a new DASD + LVM + bootc root
## 1. Bring DASD online
```
cio_ignore -r 4b2e
chccwdev -e 4b2e
dasdfmt -b 4096 -d cdl -p /dev/disk/by-path/ccw-0.0.4b2e
```
## 2. LVM Setup
```
pvcreate /dev/dasda
vgcreate rhelvg /dev/dasda
lvcreate -L 40G -n root rhelvg
mkfs.xfs /dev/rhelvg/root
```
## 3. fstab
```
/dev/rhelvg/root / xfs defaults 0 0
```
## 4. Persist DASD
```
echo "0.0.4b2e 1" >> /etc/dasd.conf
dracut -f
```
## 5. zipl config (if booting from DASD)
```
parameters="root=/dev/rhelvg/root rd.dasd=0.0.4b2e rd.lvm.lv=rhelvg/root"
```






