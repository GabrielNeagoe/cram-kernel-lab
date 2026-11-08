# Hash lookup and arena-lock contention during parallel CRAM faults

**The wider hash table reduced two-worker CPU by 69% and p99 cold-fault latency by 69% at 704 MiB.** At identical occupancy (135,168 compressed entries, 231 MiB of slots), each lookup inspected 98.78 entries with 1,024 buckets and 1.22 with 65,536 buckets. With probes disabled, worker CPU fell from 9.36 to 2.88 seconds per verified GiB and p99 fell from 44.77 to 13.71 µs.

**The shorter chains reduced time spent waiting for the single arena lock.** With two workers, mean hold time went from 11.67 to 3.63 µs per acquisition. Aggregate wait fell from 4.01 to 1.01 seconds per verified GiB. With the original table, 92.9% of attempts were contended, compared with 87.0% after. The serial control recorded 0.00% and 0.00% respectively. At 480 MiB mixed, two-worker p99 changed from 12.73 to 11.01 µs.

Two-worker wide-hash capacity was **704 MiB structured** versus 384 MiB with compression off, and **544 MiB mixed** versus 384 MiB off. The original indexed arm confirmed 704 MiB structured and 544 MiB mixed. That puts structured capacity at 1.83× baseline, 64 MiB below the 768 MiB doubling target. Mixed capacity reached 1.42×. The additional hash heads occupy 256 KiB inside the existing arena ceiling.

## Same occupancy, one or two faulting workers

The structured comparison used 704 MiB of live data and the mixed comparison used 480 MiB. Both bucket counts and both worker counts had identical occupied bytes, compressed entries after fill, and sweep store counts within each workload. CPU is the sum of process CPU seconds spent per verified GiB. Throughput uses wall time. Latency is the time to first byte on sampled cold-page accesses that faulted. Kernel probes were disabled for this table. Each cell combines two fresh boots.

| Workload | Buckets | Workers | Occupied MiB | CPU s/GiB | Verified MiB/s | Fault p50 / p99 (µs) |
|---|---:|---:|---:|---:|---:|---:|
| Structured | 1,024 | 1 | 231.00 | 5.15 | 199 | 5.08 / 22.80 |
| Structured | 1,024 | 2 | 231.00 | 9.36 | 218 | 19.66 / 44.77 |
| Structured | 65,536 | 1 | 231.00 | 1.75 | 584 | 2.37 / 10.06 |
| Structured | 65,536 | 2 | 231.00 | 2.88 | 708 | 5.18 / 13.71 |
| Mixed | 1,024 | 1 | 78.75 | 1.73 | 593 | 2.21 / 7.96 |
| Mixed | 1,024 | 2 | 78.75 | 2.85 | 709 | 2.42 / 12.73 |
| Mixed | 65,536 | 1 | 78.75 | 1.23 | 831 | 2.15 / 6.92 |
| Mixed | 65,536 | 2 | 78.75 | 1.80 | 1122 | 2.25 / 11.01 |

## Chain traversal and lock acquisition

The probed runs count each inspected hash entry, including the matching entry on a hit. Contention means an immediate trylock failed. The wait interval runs from the acquisition attempt to acquiring the lock on those failures. It includes retry, interrupt and virtualization delays. Hold time spans the critical section. Hold includes hash and codec work. The [machine-readable comparison](../evidence/hash-01/comparison.json) includes totals, CPU with probes enabled, and hash, codec and hold seconds per verified GiB. A dash means there were no contended samples.

| Workload | Buckets | Workers | Nodes/lookup | ns/lookup | Contended | Hold µs/acquire | Wait µs/contended acquire | Wait s/GiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Structured | 1,024 | 1 | 98.87 | 5614 | 0.00% | 11.39 | — | 0.000 |
| Structured | 1,024 | 2 | 98.78 | 5603 | 92.86% | 11.67 | 10.99 | 4.014 |
| Structured | 65,536 | 1 | 1.22 | 142 | 0.00% | 3.17 | — | 0.000 |
| Structured | 65,536 | 2 | 1.22 | 150 | 86.98% | 3.63 | 2.94 | 1.006 |
| Mixed | 1,024 | 1 | 36.26 | 1320 | 0.00% | 3.90 | — | 0.000 |
| Mixed | 1,024 | 2 | 36.20 | 1478 | 75.67% | 4.63 | 4.34 | 0.968 |
| Mixed | 65,536 | 1 | 0.42 | 95 | 0.00% | 2.26 | — | 0.000 |
| Mixed | 65,536 | 2 | 0.42 | 89 | 62.15% | 2.69 | 2.69 | 0.492 |

## Two-worker capacity and costs at the verified limits

Boots ran compression off, 1,024 buckets, 65,536 buckets, then in reverse order. Each reported capacity passed two additional confirmations per boot. Slashes in capacity or ratio columns distinguish the two boots. Ratios use the compression-off capacity from the corresponding boot pair. The latency columns show medians of run-level percentiles. A dash denotes zero sampled cold-page faults.

| Workload | Buckets | Confirmed MiB | Next failed MiB | Multiple of off | CPU s/GiB at capacity | Fault p50 / p99 (µs) |
|---|---:|---:|---:|---:|---:|---:|
| Structured | Off | 384 | 416 | 1.0 | 0.30 | — |
| Structured | 1,024 | 704 | 736 | 1.833 | 8.88 | 18.95 / 39.68 |
| Structured | 65,536 | 704 | 736 | 1.833 | 2.86 | 5.21 / 13.94 |
| Mixed | Off | 384 | 416 | 1.0 | 0.37 | — |
| Mixed | 1,024 | 544 | 576 | 1.417 | 3.23 | 2.55 / 15.11 |
| Mixed | 65,536 | 544 | 576 | 1.417 | 1.87 | 2.24 / 11.04 |

## Implementation and reproduction

The new boot option widens the chained hash index while retaining the [indexed demand allocator](slot-search-profile.md), LZ4 and the single arena lock. The extra 256 KiB of hash heads counts against the same 256 MiB cap. The lookup, insertion and swapoff paths use the selected table. The baseline uses the original 1,024 heads. Both arms ran the same compiled image. The timing probes are disabled during normal operation and enabled only for the tagged profiling attempts.

A single process populated the workload before each measured sweep. For parallel sweeps, two threads pinned to separate vCPUs started at a barrier, then consumed disjoint blocks from the same deterministic permutation. The serial control ran on vCPU 0. The process clock covers verification, restoring and re-evicting pages, and thread setup during the sweeps. Latency samples from both workers are pooled within each run. The kernel probe counters cover the whole arena; other kernel callers can also acquire the lock. With two boots per arm, small tail-latency differences should be interpreted cautiously.

The outer VMware guest had a 768 MiB boot ceiling, two vCPUs and a 64 MiB sampled headroom floor. Each successful run verified every byte over three sweeps without disk swap I/O. The harness checked for zero cgroup OOM kills and empty entries and idle backing after each attempt. This measures the bounded guest workload. Full Ubuntu desktop capacity and scalability above two vCPUs remain open.

Recompute the tables from the [six boot records](../evidence/hash-01) with Python 3:

```sh
python tools/summarize_hash_profile.py evidence/hash-01
```

Apply the [hash patch](../patches/kernel-cram-wide-hash.patch) after the [slot-index patch](../patches/kernel-cram-slot-index.patch). The experimental arm sets `zswap.cram_wide_hash=1` with demand backing and the slot index enabled. Build the [workload](../experiments/capacity-trial.c) with `-pthread`. The [boot harness](../tools/capacity_trial_init.sh) selects the pinned worker count and probe state. The [rollback record](../evidence/hash-01/rollback.json) verifies restoration of the deployed kernel.
