#!/bin/bash
# Wait for the current build, stage modules, and boot an isolated smoke guest.
# No sudo, package installation, host boot changes, or host reboot.
set -euo pipefail
lab=${CRAM_LAB:-/srv/cram-lab}
out=${CRAM_BUILD:-$lab/build-linux-vm}
src=${CRAM_SOURCE:-$lab/linux}
exec 8>"$out/stage.lock"
flock -n 8 || exit 1
exec >"$out/stage.log" 2>&1
trap 'result=$?; printf "%s\n" "$result" > "$out/stage-exit.txt"' EXIT
flock "$out/build.lock" true
test "$(cat "$out/build-exit.txt")" = 0
release=$(cat "$out/kernelrelease.txt")
stage=$out/staging
test ! -e "$stage" || { echo 'Refusing to overwrite existing staged installation'; exit 1; }
mkdir -p "$stage/boot"
make -C "$src" O="$out" INSTALL_MOD_PATH="$stage" modules_install
install -m 644 "$out/arch/x86/boot/bzImage" "$stage/boot/vmlinuz-$release"
install -m 644 "$out/System.map" "$stage/boot/System.map-$release"
install -m 644 "$out/.config" "$stage/boot/config-$release"
depmod -b "$stage" -e -F "$out/System.map" "$release" 2>"$out/depmod-check.txt"
test ! -s "$out/depmod-check.txt" || { cat "$out/depmod-check.txt"; exit 1; }
for module in kvm kvm_intel vmw_vmci vmwgfx e1000; do
  info=$(modinfo -b "$stage" -k "$release" -F vermagic "$module")
  test "${info%% *}" = "$release"
done
du -sh "$stage" > "$out/staged-size.txt"
sha256sum "$stage/boot/"* > "$out/staged-boot-sha256.txt"
python3 "$lab/deploy-selftests-20261024/run_placement_study.py" --smoke \
  --kernel "$out/arch/x86/boot/bzImage" \
  --initrd "$lab/images/initramfs-cram-preflush.cpio.gz" > "$out/smoke-serial.txt" 2>&1
if test -n "${CRAM_APPARMOR_INITRD:-}"; then
  python3 "$lab/apparmor-study/run_placement_study.py" --apparmor --without-kunit \
    --kernel "$out/arch/x86/boot/bzImage" \
    --initrd "$CRAM_APPARMOR_INITRD" > "$out/apparmor-serial.txt" 2>&1
fi
echo 'Staged installation and disposable-guest smoke checks passed; outer VM unchanged.'
