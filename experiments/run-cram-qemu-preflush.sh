#!/usr/bin/env bash
set -euo pipefail
cd /srv/cram-lab

exec build-qemu/qemu-system-x86_64 \
  -L qemu/pc-bios \
  -machine q35,cxl=on,cxl-fmw.0.targets.0=cxl.1,cxl-fmw.0.size=1G \
  -accel kvm \
  -cpu host \
  -smp 2 \
  -m 4G,maxmem=8G,slots=8 \
  -object memory-backend-ram,id=ram0,size=4G \
  -numa node,nodeid=0,cpus=0-1,memdev=ram0 \
  -numa node,nodeid=1 \
  -object memory-backend-ram,id=cxlmem,size=256M,share=on \
  -device pxb-cxl,bus_nr=12,bus=pcie.0,id=cxl.1 \
  -device cxl-rp,port=0,bus=cxl.1,id=rp0,chassis=0,slot=2 \
  -device cxl-ct3,bus=rp0,volatile-memdev=cxlmem,id=ct3 \
  -kernel build-linux/arch/x86/boot/bzImage \
  -initrd images/initramfs-cram-preflush.cpio.gz \
  -append 'console=ttyS0,115200 earlyprintk=serial rdinit=/init loglevel=7 panic=-1' \
  -nographic \
  -monitor none \
  -no-reboot \
  -nic none
