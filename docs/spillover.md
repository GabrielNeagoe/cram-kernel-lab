# Spilling compressed entries into the peer shard under 7:1 load

**Structured capacity was 576 MiB with spill disabled and 704 MiB with spill enabled.** Compression off sustained 384 MiB structured. Mixed capacity was 512 MiB and 512 MiB with spilling disabled and enabled respectively. Each design ran on two fresh outer VMware boots. The structured workload reached 1.83× the compression-off baseline, 64 MiB short of the 768 MiB doubling target.

At a fixed 512 MiB structured working set, spillover stored **49,956–49,965 entries per attempt** in the peer shard across fill and three restore/re-eviction sweeps. It restored 37,467–37,473 and explicitly invalidated 12,489–12,492 per attempt. The original policy recorded 49,956–49,965 full rejections. Each attempt ended with zero live spilled entries and zero arena entries across both shards.

With probes disabled, CPU went from 4.80 to 5.14 seconds per verified GiB and p99 fault latency from 44.84 to 46.70 µs. The enabled arm restored 98,304 pages per sweep, compared with 85,814 when spilling was disabled. Two saturated spill-off runs recorded one more restore than successful re-eviction across three sweeps. All bytes passed verification. The live workload and verified bytes match. The enabled arm may compress pages that the disabled arm keeps resident.

## Fixed workload with four pinned workers

Each cell uses six probe-disabled runs (three independent processes per boot, four threads per process) for process CPU, throughput and p99, and two separate kernel-probed runs for contention and wait. CPU includes all four threads, integrity checks, faults and re-eviction during three sweeps. A run’s p99 is the 99th percentile of sampled first-byte accesses that faulted. The table reports the median of those run-level p99 values.

| Workload and live MiB | Policy | Entries after fill | Contended | Wait s/GiB | CPU s/GiB | Verified MiB/s | Fault p99 (µs) |
|---|---|---:|---:|---:|---:|---:|---:|
| structured-384 | Spill off | 73,728 | 83.9% | 2.91 | 4.96 | 804 | 44.06 |
| structured-384 | Spill on | 73,728 | 84.9% | 2.97 | 4.96 | 802 | 45.71 |
| structured-512 | Spill off | 85,814 | 83.6% | 2.86 | 4.80 | 833 | 44.84 |
| structured-512 | Spill on | 98,304 | 71.5% | 3.21 | 5.14 | 776 | 46.70 |
| mixed-384 | Spill off | 36,864 | 78.0% | 1.60 | 3.05 | 1282 | 28.95 |
| mixed-384 | Spill on | 36,864 | 78.8% | 1.63 | 3.06 | 1284 | 28.33 |

At 384 MiB, both policies had the same live compressed entries and slot occupancy within each profile and neither needed to spill. At 512 MiB, the comparison holds the live application allocation constant. The counters report the actual compressed occupancy under each policy.

## Usable capacity and cost at each policy's limit

| Workload | Policy | Confirmed MiB | Next failed MiB | Multiple of compression off | CPU s/GiB at capacity | Fault p99 (µs) |
|---|---|---:|---:|---:|---:|---:|
| Structured | Compression off | 384 | 416 | 1.0 | 0.33 | — |
| Structured | Spill off | 576 | 608 | 1.5 | 4.69 | 42.99 |
| Structured | Spill on | 704 | 736 | 1.833 | 5.39 | 49.31 |
| Mixed | Compression off | 384 | 416 | 1.0 | 0.40 | — |
| Mixed | Spill off | 512 | 544 | 1.333 | 3.09 | 29.82 |
| Mixed | Spill on | 512 | 544 | 1.333 | 3.12 | 28.92 |

The largest live footprint had to pass two independent confirmations per boot with a 64 MiB sampled headroom floor. Capacity was searched in 32 MiB increments. Slashes separate paired boots that differed. The compression-off case has no arena and used no swap. No cold faults were sampled in its timed sweeps.

## Placement and locking

The [spillover patch](../patches/kernel-cram-spill.patch) lets shard zero spill into shard one on a slot-fit failure. It keeps the hot key's home lock, acquires the peer lock in that fixed order and publishes the already-compressed bytes directly into a peer slot. Each shard retains a 128 MiB limit inside the 256 MiB cap. Both placement paths reserve the rounded entry charge once through the existing memcg budget. A failed peer fit or allocation follows the normal rejection path. Spilling is opt-in and one-way in this experiment.

A per-bucket count tells the home lookup when to check the peer hash. A full-key comparison resolves collisions. Replacing, restoring, invalidating or removing an entry during swapoff updates the count under the peer lock. The extra 128 KiB table is charged inside the receiver's existing limit. No swap-entry payload is stored in the lookup table.

Contention counts failed immediate arena-lock acquisitions. Wait is summed over acquisitions per verified GiB. A spill operation can acquire both locks, so its denominator can differ between policies. Home hold time includes time nested in the peer, and hold times across locks can overlap. Memcg reservation lock contention is outside these measurements. In the enabled arm, the legacy `reject_full` counter counts local fit failures even when spillover succeeds. `spill_tries` and `spill_stores` distinguish attempted from completed transfers. All placement counters count operations and can count a page again after it is restored and evicted.

## Validation and reproduction

Six fresh boots ran compression off, spill off, spill on, spill on, spill off and compression off. The outer guest used four vCPUs and a 768 MiB boot ceiling. Both compression policies ran the same kernel, wide-hash indexed LZ4, two shards, and the [same 7:1 synthetic routing](uneven-shards.md). One quarter of pages were incompressible and locked. The mixed workload also made half the cold pages incompressible. Every accepted attempt verified every byte three times with zero disk swap I/O. The harness checked for cgroup OOM kills, return to idle backing, and balanced spill publication/removal counts even after headroom exits. Host samples across the trials showed one VMware VM process. The [rollback record](../evidence/spill-01/rollback.json) verifies the deployed 4 MiB LZ4 kernel was restored.

Apply the spillover patch after the [skew routing patch](../patches/kernel-cram-skew-trial.patch). The opt-in boot flag is `zswap.cram_spill=1` with 7:1 routing enabled. The [boot harness](../tools/capacity_trial_init.sh), [workload](../experiments/capacity-trial.c), [raw records](../evidence/spill-01) and [comparison](../evidence/spill-01/comparison.json) are included. Recompute the results with Python 3:

```sh
python tools/summarize_spill_trial.py evidence/spill-01
```
