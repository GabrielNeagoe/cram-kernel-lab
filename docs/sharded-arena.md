# One versus two arena locks in wide-hash indexed LZ4

At 704 MiB structured with two workers, sharding changed arena contention from **86.6% to 33.0%** and aggregate waiting from **1.01 to 0.29 seconds per verified GiB**. Worker CPU changed from 2.85 to 2.13 s/GiB and p99 fault latency from 13.89 to 12.16 µs. At 480 MiB mixed, CPU changed from 1.79 to 1.49 s/GiB and p99 from 10.65 to 9.11 µs.

Two-shard capacity reached **736 / 704 MiB structured** and **544 MiB mixed**. The paired multiples of compression off were 1.917 / 1.833 and 1.417, respectively. The one-lock control reached 704 MiB on both structured boots. The best sharded result came within 32 MiB of doubling the 384 MiB baseline, but the extra capacity step did not repeat. The idle arena backing was 576 KiB with one lock and 736 KiB with two.

## Performance at matched occupancy

The 704 MiB structured workload held 135,168 compressed entries occupying 231 MiB of slots. The 480 MiB mixed workload held 46,080 entries occupying 78.75 MiB. The offline check requires identical counts per workload across both designs, worker counts and boots, and identical store/load totals across the sweeps. Each performance cell summarizes six probe-disabled runs (three per boot). Each run verified three full shuffled sweeps. CPU sums the process threads, throughput uses elapsed time, and fault percentiles summarize timed first-byte loads with a thread page fault. Reported percentiles are medians of the individual run percentiles.

| Workload | Locks | Workers | CPU s/GiB | Verified MiB/s | Fault p50 / p99 (µs) |
|---|---|---:|---:|---:|---:|
| Structured | One | 1 | 1.69 | 605 | 2.31 / 7.45 |
| Structured | One | 2 | 2.85 | 717 | 5.25 / 13.89 |
| Structured | Two | 1 | 1.69 | 606 | 2.30 / 7.08 |
| Structured | Two | 2 | 2.13 | 959 | 2.73 / 12.16 |
| Mixed | One | 1 | 1.23 | 835 | 2.11 / 6.37 |
| Mixed | One | 2 | 1.79 | 1132 | 2.20 / 10.65 |
| Mixed | Two | 1 | 1.22 | 842 | 2.10 / 5.89 |
| Mixed | Two | 2 | 1.49 | 1357 | 2.16 / 9.11 |

## Contention and partition balance

One separate instrumented run per boot measured lock behavior. Contended acquisitions failed an immediate trylock. Wait is elapsed acquisition time on those failures. Hold time includes hash and codec work under the owning lock. Wait and hold sum across workers and can overlap wall time. The probes cover arena locks only. The shared memcg reservation lock and other kernel locks are outside these counters. The last column shows each boot’s share of compressed stores sent to shard zero. Full probe totals and probe-disabled per-boot measurements are in the [comparison](../evidence/shard-01/comparison.json).

| Workload | Locks | Workers | Nodes/lookup | Contended | Hold µs/acquire | Wait s/GiB | Shard zero stores (%) |
|---|---|---:|---:|---:|---:|---:|---:|
| Structured | One | 1 | 1.22 | 0.00% | 3.10 | 0.000 | 100 |
| Structured | One | 2 | 1.31 | 86.64% | 3.61 | 1.009 | 100 |
| Structured | Two | 1 | 1.22 | 0.00% | 3.07 | 0.000 | 49.999 |
| Structured | Two | 2 | 1.31 | 32.96% | 3.41 | 0.287 | 49.999 |
| Mixed | One | 1 | 0.42 | 0.00% | 2.25 | 0.000 | 100 |
| Mixed | One | 2 | 0.48 | 62.12% | 2.66 | 0.490 | 100 |
| Mixed | Two | 1 | 0.42 | 0.00% | 2.24 | 0.000 | 50.002 / 50.015 |
| Mixed | Two | 2 | 0.42 | 27.50% | 2.55 | 0.190 | 50.009 / 50.013 |

## Capacity against compression off

Each capacity passed two independent two-worker confirmations per boot. The next failed step crossed the 64 MiB headroom floor. A slash in a capacity or ratio cell separates different results on the two boots. A dash denotes zero sampled faults.

| Workload | Locks | Confirmed MiB | Next failed MiB | Multiple of off | CPU s/GiB at capacity | Fault p50 / p99 (µs) |
|---|---|---:|---:|---:|---:|---:|
| Structured | Off | 384 | 416 | 1.0 | 0.30 | — |
| Structured | One | 704 | 736 | 1.833 | 2.83 | 5.21 / 13.94 |
| Structured | Two | 736 / 704 | 768 / 736 | 1.917 / 1.833 | 2.13 | 2.74 / 12.44 |
| Mixed | Off | 384 | 416 | 1.0 | 0.36 | — |
| Mixed | One | 544 | 576 | 1.417 | 1.80 | 2.19 / 10.66 |
| Mixed | Two | 544 | 576 | 1.417 | 1.51 | 2.18 / 8.56 |

## Implementation and limits

The [patch](../patches/kernel-cram-two-shards.patch) refactors the wide-hash arena into independently locked state. A low bit of the 16-bit bucket number chooses the shard, and the remaining 15 bits select its bucket. Both designs retain 65,536 hash heads in all. Each shard owns its slot index, 16 KiB backing chunks, scratch buffer, LZ4 workspace and counters. The two-shard design gives each shard a fixed 128 MiB share of the 256 MiB maximum. A full shard cannot borrow unused capacity from its neighbor. Swap invalidation follows the same key routing as store and load, and swapoff visits both shards. Whole-arena peaks reported by the two-shard debug interface are sums of local peaks, an upper bound on simultaneous use.

The outer VMware guest used two vCPUs, a 768 MiB boot ceiling, the same LZ4 kernel image and the [previous pinned-worker workload](parallel-hash-profile.md). Six one-shot boots ran off, one lock, two locks, two locks, one lock, off. One process populated pages before each timed test. In two-worker runs, pinned threads started at a barrier and verified disjoint shuffled blocks. The protocol required full data integrity, zero disk swap I/O, zero cgroup OOM kills and empty entries and idle backing after each worker process. The VM returned to the deployed 4 MiB LZ4 kernel. These results cover a balanced synthetic workload on two vCPUs. Shard imbalance, higher concurrency, and a full Ubuntu application need separate measurement.

Apply the sharding patch after the [wide-hash patch](../patches/kernel-cram-wide-hash.patch). The opt-in boot flag is `zswap.cram_two_shards=1` with wide hash, indexed demand backing and LZ4 enabled. The [raw records and rollback](../evidence/shard-01), [workload](../experiments/capacity-trial.c), and [boot harness](../tools/capacity_trial_init.sh) accompany this report. Recompute the measurements from the repository root:

```sh
python tools/summarize_hash_profile.py --shards evidence/shard-01
```
