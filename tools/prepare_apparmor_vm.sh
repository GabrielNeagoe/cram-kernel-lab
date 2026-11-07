#!/bin/bash
# Separate deployment build, preserving the installed first candidate.
set -euo pipefail
lab=/srv/cram-lab
src=$lab/linux-apparmor
out=$lab/build-linux-vm-aa
test ! -e "$out/.config"
mkdir -p "$out"
cp "$lab/build-linux-vm/.config" "$out/.config"
"$src/scripts/config" --file "$out/.config" --set-str LOCALVERSION '-cram-vm-aa1' \
  -d LOCALVERSION_AUTO -d KUNIT -d SECURITY_APPARMOR_KUNIT_TEST \
  -d CXL_REGION_INVALIDATION_TEST
make -C "$src" O="$out" olddefconfig > "$out/configure.log" 2>&1
make -s -C "$src" O="$out" kernelrelease > "$out/kernelrelease.txt"
for setting in CRAM=y FUSION=y FUSION_SPI=y EXT4_FS=y KVM=m KVM_INTEL=m \
  VMWARE_VMCI=m DRM_VMWGFX=m E1000=m SQUASHFS=y SECURITY_APPARMOR=y; do
  grep -qx "CONFIG_$setting" "$out/.config"
done
grep -qx '# CONFIG_CXL_REGION_INVALIDATION_TEST is not set' "$out/.config"
sha256sum "$out/.config" > "$out/config-sha256.txt"
git -C "$src" rev-parse HEAD > "$out/source-commit.txt"
git -C "$src" diff --binary > "$out/source.patch"
sha256sum "$out/source.patch" > "$out/source-patch-sha256.txt"
"$src/scripts/diffconfig" "$lab/build-linux-vm/.config" "$out/.config" > "$out/config-diff.txt"
cat "$out/kernelrelease.txt"
