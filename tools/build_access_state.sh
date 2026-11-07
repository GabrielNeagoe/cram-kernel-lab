#!/usr/bin/env bash
# Build the matched access-state controls on the existing Ubuntu lab host.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output=$(mktemp -d /srv/cram-lab/access-state-XXXXXXXX)
mkdir -p "$output/overlay/bin"
cp "$project/experiments/cram-placement-probe.c" "$project/experiments/cram-access-state-probe.c" "$project/tools/run_placement_study.py" "$output/"
gcc -static -O2 -Wall -Wextra -Werror -o "$output/overlay/bin/cram-access-state-probe" "$output/cram-access-state-probe.c"
cp /srv/cram-lab/images/initramfs-cram-preflush.cpio.gz "$output/placement.cpio.gz"
(cd "$output/overlay" && find . -print0 | cpio --null -o -H newc --owner=0:0 | gzip -c) >> "$output/placement.cpio.gz"
printf 'Build directory: %s\n' "$output"
printf 'Run: python3 %s/run_placement_study.py --access-state\n' "$output"
