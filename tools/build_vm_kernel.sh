#!/bin/bash
# Run unprivileged; builds image and all configured modules, never installs.
set -euo pipefail
lab=${CRAM_LAB:-/srv/cram-lab}
out=${CRAM_BUILD:-$lab/build-linux-vm}
src=${CRAM_SOURCE:-$lab/linux}
exec 9>"$out/build.lock"
flock -n 9 || exit 1
date -u +%FT%TZ > "$out/build-started.txt"
set +e
if ! command -v gawk >/dev/null; then
  echo 'Missing GNU awk: install gawk before building this configuration.' > "$out/build.log"
  result=127
else
  make -C "$src" O="$out" -j"${JOBS:-6}" bzImage modules > "$out/build.log" 2>&1
  result=$?
fi
set -e
date -u +%FT%TZ > "$out/build-finished.txt"
printf '%s\n' "$result" > "$out/build-exit.txt"
exit "$result"
