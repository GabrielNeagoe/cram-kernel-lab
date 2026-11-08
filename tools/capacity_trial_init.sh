#!/bin/sh
# PID 1 for a disposable x86-64 VMware capacity boot (also supports scratch KVM smoke).
export PATH=/bin
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t debugfs debugfs /sys/kernel/debug
mount -t cgroup2 none /sys/fs/cgroup
mode=
dataset=
case_id=
smoke=0
for v in $(cat /proc/cmdline); do
    case "$v" in
        cram.trial=*) mode=${v#*=};;
        cram.volume=*) dataset=${v#*=};;
        cram.run=*) case_id=${v#*=};;
        cram.smoke=1) smoke=1;;
    esac
done
finish() {
    rc=$?
    trap - EXIT
    echo "CRAM_CAPACITY_EXIT $rc" >/dev/console
    if [ -n "$out" ] && [ -d "$out" ]; then
        echo "$rc" >"$out/exit-status"
        dmesg | tail -120 >"$out/kernel-tail.private"
    fi
    sync
    if [ "$smoke" = 1 ]; then poweroff -f; else reboot -f; fi
    sleep 300
}
trap finish EXIT
set -eu
case "$mode" in off|lz4|zstd1) ;; *) exit 2;; esac
case "$case_id" in ''|*[!a-z0-9]*) exit 2;; esac
out=
if [ "$smoke" = 1 ]; then
    out=/tmp/smoke
    mkdir "$out"
    swap=/dev/vda
else
    test -n "$dataset"
    disk=
    for retry in $(seq 1 30); do
        for dev in /dev/sd*1; do
            if [ "$(/volid -s UUID -o value "$dev" 2>/dev/null || true)" = "$dataset" ]; then disk=$dev; break; fi
        done
        [ -z "$disk" ] || break
        sleep 1
    done
    test -b "$disk"
    mount -t ext4 "$disk" /lab
    root=/lab/capacity-trial-01
    test -f "$root/volume-marker"
    mkdir "$root/result-$case_id"
    out="$root/result-$case_id"
    swap="$root/trial.swap"
fi
exec >"$out/init.log" 2>&1
# Return to the normal guest even if the benchmark stalls.
( sleep 3600; echo CRAM_WATCHDOG >/dev/console; sync; reboot -f ) &
params=/sys/module/zswap/parameters
debug=/sys/kernel/debug/zswap
cg=/sys/fs/cgroup/bench
echo +memory > /sys/fs/cgroup/cgroup.subtree_control
mkdir "$cg"
echo 0 > "$cg/memory.zswap.writeback"
echo 0 >/proc/sys/vm/page-cluster
echo never >/sys/kernel/mm/transparent_hugepage/enabled
available_total=$(awk '/^MemTotal:/{print $2}' /proc/meminfo)
test "$available_total" -lt 1048576
test "$available_total" -gt 393216
test "$(cat /sys/devices/system/cpu/online)" = '0-1'
if [ "$mode" = off ]; then
    test "$(cat "$params/enabled")" = N
    test "$(cat "$params/cram_arena_mb")" = 0
    echo 0 > "$cg/memory.swap.max"
    arena=0
    overhead=0
else
    test "$(cat "$params/enabled")" = Y
    test "$(cat "$params/cram_arena_mb")" = 256
    if [ "$mode" = zstd1 ]; then test "$(cat "$params/cram_zstd1")" = Y
    else test "$(cat "$params/cram_zstd1")" = N; fi
    swapon "$swap"
    arena=$(cat "$debug/arena_backing_bytes")
    overhead=$(cat "$debug/arena_overhead_bytes")
    test "$arena" -eq 268435456
fi
printf '{"mode":"%s","memtotal_kib":%s,"arena_bytes":%s,"arena_overhead_bytes":%s,"memory_floor_kib":65536,"grid_mib":32,"cpus":2,"swap_writeback":false}\n' "$mode" "$available_total" "$arena" "$overhead" >"$out/config.json"
# Each point has a fresh process; all previous entries must have unwound.
point() {
    phase=$1
    size=$2
    file="$out/$profile-$phase-$size.json"
    before=$(awk '$1=="pswpout"{print $2}' /proc/vmstat)
    rc=0
    /bin/sh -c 'echo $$ > /sys/fs/cgroup/bench/cgroup.procs; exec /bench "$@"' sh "$size" "$profile" >"$file" || rc=$?
    test "$(awk '$1=="pswpout"{print $2}' /proc/vmstat)" = "$before" || exit 5
    test "$(awk '$1=="oom_kill"{print $2}' "$cg/memory.events")" = 0 || exit 5
    if [ "$mode" != off ]; then test "$(cat "$debug/arena_entries")" = 0 || exit 5; fi
    case "$rc" in 0) grep -q '"status":"pass"' "$file" || exit 5;;
        42) grep -q '"status":"headroom"' "$file" || exit 5;;
        *) exit "$rc";; esac
    if [ "$smoke" = 1 ]; then cat "$file" >/dev/console; fi
    sync
    return "$rc"
}
if [ "$smoke" = 1 ]; then
    profile=structured
    point common 128
    exit 0
fi
for profile in structured mixed; do
    for n in 1 2 3; do point common$n 128; done
    lo=128
    hi=256
    while point search "$hi"; do
        lo=$hi
        if [ "$lo" -ge 2048 ]; then break; fi
        hi=$((hi*2))
    done
    while [ $((hi-lo)) -gt 32 ]; do
        mid=$(((hi+lo)/64*32))
        if point search "$mid"; then lo=$mid; else hi=$mid; fi
    done
    while true; do
        if point confirm1 "$lo" && point confirm2 "$lo"; then break; fi
        hi=$lo
        lo=$((lo-32))
        test "$lo" -ge 128 || exit 5
    done
    printf '{"verified_mib":%s,"upper_mib":%s}\n' "$lo" "$hi" >"$out/$profile-bound.json"
done
