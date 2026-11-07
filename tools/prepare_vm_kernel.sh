#!/bin/bash
# Prepare a separate Ubuntu-configuration-based experimental build; never install it.
set -euo pipefail
lab=${CRAM_LAB:-/srv/cram-lab}
src=$lab/linux
out=$lab/build-linux-vm
stock=${STOCK_CONFIG:-/boot/config-7.0.0-38-generic}
test -f "$stock"
test ! -e "$out/.config" || { echo 'Candidate already exists; refusing to overwrite'; exit 1; }
mkdir -p "$out"
cp "$stock" "$out/ubuntu-baseline.config"
cp "$stock" "$out/.config"
cfg=$src/scripts/config
"$cfg" --file "$out/.config" --set-str LOCALVERSION '-cram-vm' --disable LOCALVERSION_AUTO \
  --enable CRAM --enable NUMA --enable MIGRATION --enable MEMORY_HOTPLUG \
  --enable FUSION --enable FUSION_SPI --enable SCSI --enable BLK_DEV_SD --enable EXT4_FS \
  --module KVM --module KVM_INTEL --module VMWARE_VMCI --module VMWARE_PVSCSI \
  --module DRM_VMWGFX --module E1000 --enable SQUASHFS --enable SECURITY_APPARMOR \
  --enable CXL_BUS --enable CXL_PCI --enable CXL_ACPI --enable CXL_MEM \
  --enable CXL_PORT --enable CXL_REGION --enable DEV_DAX --enable DEV_DAX_KMEM \
  --disable CXL_REGION_INVALIDATION_TEST \
  --set-str SYSTEM_TRUSTED_KEYS '' --set-str SYSTEM_REVOCATION_KEYS '' \
  --disable DEBUG_INFO --disable DEBUG_INFO_DWARF5 --disable DEBUG_INFO_BTF \
  --enable DEBUG_INFO_NONE --disable DEBUG_INFO_BTF_MODULES
make -C "$src" O="$out" olddefconfig > "$out/configure.log" 2>&1
"$src/scripts/diffconfig" "$stock" "$out/.config" > "$out/config-diff.txt"
make -s -C "$src" O="$out" kernelrelease > "$out/kernelrelease.txt"
for setting in CRAM=y FUSION=y FUSION_SPI=y EXT4_FS=y KVM=m KVM_INTEL=m \
  VMWARE_VMCI=m DRM_VMWGFX=m E1000=m SQUASHFS=y SECURITY_APPARMOR=y; do
  grep -qx "CONFIG_$setting" "$out/.config" || { echo "Missing required $setting"; exit 1; }
done
grep -qx '# CONFIG_CXL_REGION_INVALIDATION_TEST is not set' "$out/.config"
git -C "$src" rev-parse HEAD > "$out/source-commit.txt"
sha256sum "$out/.config" "$stock" > "$out/config-sha256.txt"
echo 'Configuration checks passed. No kernel has been installed.'
cat "$out/kernelrelease.txt"
