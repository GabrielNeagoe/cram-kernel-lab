#!/usr/bin/env bash
# Run from a checkout of this evidence repository on the existing Ubuntu lab host.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
case "${1:-}" in ''|--pin-cpu0) ;; *) printf 'Usage: %s [--pin-cpu0]\n' "$0" >&2; exit 2;; esac
output=$(mktemp -d /srv/cram-lab/phase-study-XXXXXXXX)
mkdir -p "$output/overlay/bin"
cp "$project/experiments/cram-bench-phased.c" "$project/tools/capture_phases.py" "$output/"
source_file="$output/cram-bench-phased.c"
if [[ "${1:-}" == --pin-cpu0 ]]; then
  cp "$project/experiments/cram-partial-inspect.h" "$project/tools/make_partial_probe.py" "$output/"
  python3 "$output/make_partial_probe.py" "$source_file" "$output/cram-bench-pinned.c" --pin-cpu0
  source_file="$output/cram-bench-pinned.c"
fi
gcc -static -O2 -Wall -Wextra -Werror -o "$output/overlay/bin/cram-bench-phased" "$source_file"
cp /srv/cram-lab/images/initramfs-cram-preflush.cpio.gz "$output/phased.cpio.gz"
(cd "$output/overlay" && find . -print0 | cpio --null -o -H newc --owner=0:0 | gzip -c) >> "$output/phased.cpio.gz"
printf 'Build directory: %s\n' "$output"
printf 'Trial: python3 %s/capture_phases.py --dry-run\n' "$output"
printf 'Capture: sudo python3 %s/capture_phases.py\n' "$output"
