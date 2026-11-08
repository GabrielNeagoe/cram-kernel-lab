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
hotprobe=0
series=capacity-trial-01
for v in $(cat /proc/cmdline); do
    case "$v" in
        cram.trial=*) mode=${v#*=};;
        cram.volume=*) dataset=${v#*=};;
        cram.run=*) case_id=${v#*=};;
        cram.series=demand-01|cram.series=profile-01|cram.series=hash-01|cram.series=shard-01|cram.series=skew-01|cram.series=spill-01) series=${v#*=};;
        cram.hotprobe=1) hotprobe=1;;
        cram.smoke=1) smoke=1;;
    esac
done
is_skew_series() {
    [ "$series" = skew-01 ] || [ "$series" = spill-01 ]
}
uses_skew() {
    [ "$mode" = skew ] || [ "$mode" = spill ]
}
is_hash_trial() {
    [ "$series" = hash-01 ] || { [ "$series" = shard-01 ] || is_skew_series; }
}
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
case "$mode" in off|lz4|zstd1|demand|indexed|wide|sharded|skew|spill) ;; *) exit 2;; esac
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
    root=/lab/$series
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
cpus=2
if is_skew_series; then cpus=4; fi
test "$(cat /sys/devices/system/cpu/online)" = "0-$((cpus-1))"
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
    if [ "$series" != capacity-trial-01 ]; then
        if [ "$mode" = demand ] || [ "$mode" = indexed ] || [ "$mode" = wide ] || [ "$mode" = sharded ] || uses_skew; then test "$(cat "$params/cram_demand")" = Y
        else test "$(cat "$params/cram_demand")" = N; fi
    fi
    if [ "$series" = profile-01 ] || is_hash_trial; then
        if [ "$mode" = indexed ] || [ "$mode" = wide ] || [ "$mode" = sharded ] || uses_skew; then test "$(cat "$params/cram_index")" = Y
        else test "$(cat "$params/cram_index")" = N; fi
    fi
    if is_hash_trial; then
        if [ "$mode" = wide ] || [ "$mode" = sharded ] || uses_skew; then test "$(cat "$params/cram_wide_hash")" = Y
        else test "$(cat "$params/cram_wide_hash")" = N; fi
    fi
    if { [ "$series" = shard-01 ] || is_skew_series; }; then
        if [ "$mode" = sharded ] || uses_skew; then
            test "$(cat "$params/cram_two_shards")" = Y
            export CRAM_TWO_SHARDS=1
        else test "$(cat "$params/cram_two_shards")" = N; fi
    fi
    if is_skew_series; then
        if uses_skew; then test "$(cat "$params/cram_skew_test")" = Y
        else test "$(cat "$params/cram_skew_test")" = N; fi
    fi
    if [ "$series" = spill-01 ]; then
        if [ "$mode" = spill ]; then test "$(cat "$params/cram_spill")" = Y
        else test "$(cat "$params/cram_spill")" = N; fi
    fi
    swapon "$swap"
    arena=$(cat "$debug/arena_backing_bytes")
    overhead=$(cat "$debug/arena_overhead_bytes")
    test "$arena" -eq 268435456
fi
printf '{"mode":"%s","memtotal_kib":%s,"arena_bytes":%s,"arena_overhead_bytes":%s,"memory_floor_kib":65536,"grid_mib":32,"cpus":%s,"swap_writeback":false}\n' "$mode" "$available_total" "$arena" "$overhead" "$cpus" >"$out/config.json"
extra_arg=
idle=0
if [ "$series" != capacity-trial-01 ] && [ "$mode" != off ]; then
    extra_arg=--arena-stats
    idle=$(cat "$debug/arena_resident_bytes")
    if [ "$mode" = demand ] || [ "$mode" = indexed ] || [ "$mode" = wide ] || [ "$mode" = sharded ] || uses_skew; then test "$idle" -eq "$overhead"
    else test "$idle" -eq "$arena"; fi
fi
# Each point has a fresh process; all previous entries must have unwound.
point() {
    phase=$1
    size=$2
    file="$out/$profile-$phase-$size.json"
    before=$(awk '$1=="pswpout"{print $2}' /proc/vmstat)
    counters=
    if is_skew_series && [ -n "${CRAM_TWO_SHARDS:-}" ]; then
        counters="reject_full peer_checks peer_busy peer_fit peer_backed shard0_stores shard1_stores"
        if [ "$series" = spill-01 ]; then
            counters="$counters spill_tries spill_stores spill_loads spill_removes spill_invalidates"
        fi
        for name in $counters; do cat "$debug/arena_$name" >"$out/before-$name"; done
    fi
    rc=0
    /bin/sh -c 'echo $$ > /sys/fs/cgroup/bench/cgroup.procs; exec /bench "$@"' sh "$size" "$profile" ${extra_arg:+"$extra_arg"} >"$file" || rc=$?
    test "$(awk '$1=="pswpout"{print $2}' /proc/vmstat)" = "$before" || exit 5
    test "$(awk '$1=="oom_kill"{print $2}' "$cg/memory.events")" = 0 || exit 5
    if [ "$mode" != off ]; then test "$(cat "$debug/arena_entries")" = 0 || exit 5; fi
    if [ -n "$extra_arg" ]; then
        test "$(cat "$debug/arena_resident_bytes")" -eq "$idle" || exit 5
        test "$(cat "$debug/arena_resident_peak_bytes")" -le "$arena" || exit 5
    fi
    if [ "$series" = spill-01 ] && [ "$mode" != off ]; then
        test "$(cat "$debug/arena_spill_live")" -eq 0 || exit 5
        test "$(cat "$debug/arena_spill_stores")" -eq "$(cat "$debug/arena_spill_removes")" || exit 5
    fi
    if [ -n "$counters" ]; then
        # Attach per-attempt deltas even when the headroom guard rejected the point.
        sed 's/}$//' "$file" | tr -d '\n' >"$out/with-counters"
        for name in $counters; do
            value=$(cat "$debug/arena_$name")
            printf ',"%s":%s' "$name" "$((value-$(cat "$out/before-$name")))" >>"$out/with-counters"
        done
        for name in local_used peer_used local_backing peer_backing request; do
            printf ',"last_%s":%s' "$name" "$(cat "$debug/arena_last_$name")" >>"$out/with-counters"
        done
        echo '}' >>"$out/with-counters"
        mv "$out/with-counters" "$file"
    fi
    case "$rc" in 0) grep -q '"status":"pass"' "$file" || exit 5;;
        42) grep -q '"status":"headroom"' "$file" || exit 5;;
        *) exit "$rc";; esac
    if [ "$smoke" = 1 ]; then cat "$file" >/dev/console; fi
    sync
    return "$rc"
}
if is_hash_trial; then
    export CRAM_HASH_TRIAL=1 CRAM_WORKERS=$cpus
fi
if [ "$smoke" = 1 ]; then
    if is_hash_trial && [ "$mode" != off ]; then
        echo 1 >"$debug/arena_profiling"
        extra_arg=--profile
    fi
    profile=structured
    if [ "$series" = spill-01 ] && [ "$mode" = spill ]; then
        point common 480
        test "$(cat "$debug/arena_spill_loads")" -gt 0
        test "$(cat "$debug/arena_spill_invalidates")" -gt 0
    else point common 128; fi
    exit 0
fi
if [ "$hotprobe" = 1 ]; then
    test "$series" = skew-01
    test -n "${CRAM_TWO_SHARDS:-}"
    profile=structured
    for size in 480 512; do
        for n in 1 2; do
            point "hotplain$n" "$size" || test "$?" -eq 42
            echo 1 >"$debug/arena_profiling"
            extra_arg=--profile
            point "hotprobe$n" "$size" || test "$?" -eq 42
            echo 0 >"$debug/arena_profiling"
            extra_arg=--arena-stats
        done
    done
    exit 0
fi
for profile in structured mixed; do
    for n in 1 2 3; do point common$n 128; done
    if [ "$series" = profile-01 ] && [ "$mode" != off ]; then
        if [ "$profile" = structured ]; then sizes=576; else sizes=192; fi
        if [ "$profile" = structured ] && [ "$mode" != lz4 ]; then sizes="576 704"; fi
        for size in $sizes; do
            point matched "$size" || test "$?" -eq 42
            echo 1 >"$debug/arena_profiling"
            extra_arg=--profile
            point timed "$size" || test "$?" -eq 42
            echo 0 >"$debug/arena_profiling"
            extra_arg=--arena-stats
        done
    fi
    if [ "$series" = spill-01 ] && [ "$mode" != off ]; then
        sizes=384
        if [ "$profile" = structured ]; then sizes="384 512"; fi
        for size in $sizes; do
            for n in 1 2 3; do
                point "load$n" "$size" || test "$?" -eq 42
            done
            echo 1 >"$debug/arena_profiling"
            extra_arg=--profile
            point measured "$size" || test "$?" -eq 42
            echo 0 >"$debug/arena_profiling"
            extra_arg=--arena-stats
        done
    elif is_hash_trial && [ "$mode" != off ]; then
        if [ "$profile" = structured ]; then size=704; else size=480; fi
        counts="1 2"
        if is_skew_series; then size=384; counts="2 4"; fi
        for workers in $counts; do
            export CRAM_WORKERS=$workers
            point "plain$workers" "$size" || test "$?" -eq 42
            echo 1 >"$debug/arena_profiling"
            extra_arg=--profile
            point "probe$workers" "$size" || test "$?" -eq 42
            echo 0 >"$debug/arena_profiling"
            extra_arg=--arena-stats
            if { [ "$series" = shard-01 ] || is_skew_series; }; then
                point "repeatA$workers" "$size" || test "$?" -eq 42
                point "repeatB$workers" "$size" || test "$?" -eq 42
            fi
        done
    fi
    if is_skew_series && [ "$mode" != off ] && [ "$profile" = structured ]; then
        point stress 512 || test "$?" -eq 42
    fi
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

if [ "$series" != capacity-trial-01 ] && [ -n "$extra_arg" ]; then
    printf '{"idle_bytes":%s,"peak_bytes":%s,"allocation_failures":%s}\n' "$idle" \
        "$(cat "$debug/arena_resident_peak_bytes")" "$(cat "$debug/arena_chunk_alloc_failures")" >"$out/backing.json"
fi
