#!/bin/bash
# Run from this evidence repository on the existing Ubuntu lab host.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
lab=${CRAM_LAB:-/srv/cram-lab}
out=$(mktemp -d "$lab/deployment-tests-XXXXXXXX")
mkdir -p "$out/overlay/bin"
for name in cram_readable_tool cram_fork_tool cram_ops_tool; do
  cp "$lab/linux/tools/testing/selftests/dax/$name.c" "$out/"
  gcc -static -O2 -Wall -Wextra -Werror -o "$out/overlay/bin/$name" "$out/$name.c"
done
python3 "$repo/tools/make_relocation_probe.py" "$out/cram_ops_tool.c" "$out/cram_ops_relocate_tool.c"
gcc -static -O2 -Wall -Wextra -Werror -o "$out/overlay/bin/cram_ops_relocate_tool" "$out/cram_ops_relocate_tool.c"
cp "$lab/images/initramfs-cram-preflush.cpio.gz" "$out/placement.cpio.gz"
(cd "$out/overlay" && find . -print0 | cpio --null -o -H newc --owner=0:0 | gzip -c) >> "$out/placement.cpio.gz"
cp "$repo/tools/run_placement_study.py" "$out/"
(cd "$out" && sha256sum ./*.c overlay/bin/* placement.cpio.gz) > "$out/sha256.txt"
python3 "$out/run_placement_study.py" --selftests > "$out/selftests.txt"
python3 "$out/run_placement_study.py" --relocation > "$out/relocation.txt"
printf 'Successful test transcripts: %s\n' "$out"
