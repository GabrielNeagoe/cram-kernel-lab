#!/bin/bash
set -uo pipefail
out=/srv/cram-lab/build-linux-apparmor-test
date -u +%FT%TZ > "$out/build-started.txt"
make -C /srv/cram-lab/linux-apparmor O="$out" -j6 bzImage > "$out/kernel-build.log" 2>&1
result=$?
printf '%s\n' "$result" > "$out/build-exit.txt"
date -u +%FT%TZ > "$out/build-finished.txt"
tail -20 "$out/kernel-build.log"
exit "$result"
