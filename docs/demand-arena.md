# Does a demand-sized LZ4 arena recover usable VM memory?

**Demand-sized LZ4 raised mixed capacity from 224 to 544 MiB, a 2.43× gain over fixed LZ4 and 1.42× the 384 MiB compression-off baseline.** Structured capacity reached 736 MiB, or 1.92× baseline, against 672 MiB for fixed LZ4. The 2× goal was 768 MiB. At the common 128 MiB size, demand LZ4 used 1.44× the worker CPU of fixed LZ4 on mixed data and 2.08× on structured data.

The arena used 21.62 MiB after filling the common 128 MiB mixed workload and 90.95 MiB after filling the 544 MiB workload. Fixed LZ4 retained 256 MiB at both sizes. On structured data, both demand boots confirmed 736 MiB and crossed the 64 MiB headroom floor at 768 MiB.

On each demand boot, backing peaked at 250.06 MiB across all attempts, returned to the 284 KiB control allocation after every worker, and recorded zero chunk allocation failures. These are guest allocator measurements. Host RAM residency was outside this trial.

At its own 736 MiB structured limit, demand LZ4 took 17.43 CPU seconds per verified GiB, with a 26.82 µs p99 fault. At its 544 MiB mixed limit, it took 4.18 CPU seconds per verified GiB with an 11.30 µs p99. The timed fault samples include any first read that faulted. In the common mixed run, demand LZ4 restored 12,288 compressed pages per sweep across 24,576 cold pages, so those latency percentiles describe the observed access path as a whole. Profiling at matched high occupancy would help explain the sharp increase in CPU cost.

## Verified capacity and common-size costs

The capacity column is the largest confirmed live allocation on each boot. The remaining performance and backing columns use the same 128 MiB workload across modes. A slash in a capacity cell separates differing boot results. Off had no cold-page faults in the timed samples.

| Workload | Mode | Capacity (MiB) | Multiple of off | Worker CPU s / verified GiB | Fault p50 / p99 (µs) | Arena after fill (MiB) |
|---|---|---:|---:|---:|---:|---:|
| Structured | Off | 384 | 1.0 | 0.30 | — | 0.00 |
| Structured | Fixed LZ4 | 672 | 1.75 | 1.96 | 2.41 / 7.54 | 256.00 |
| Structured | Demand LZ4 | 736 | 1.917 | 4.08 | 2.59 / 10.29 | 42.95 |
| Mixed | Off | 384 | 1.0 | 0.37 | — | 0.00 |
| Mixed | Fixed LZ4 | 224 | 0.583 | 1.28 | 2.04 / 5.48 | 256.00 |
| Mixed | Demand LZ4 | 544 | 1.417 | 1.84 | 2.08 / 6.43 | 21.62 |

## Performance at each mode’s own capacity

The first numeric column shows the next step that crossed the headroom floor on at least one attempt. The remaining columns use the four successful confirmation runs per mode and workload.

| Workload | Mode | Next failed step (MiB) | Worker CPU s / verified GiB | Verified MiB/s | Fault p50 / p99 (µs) | Arena after fill (MiB) |
|---|---|---:|---:|---:|---:|---:|
| Structured | Off | 416 | 0.29 | 3499 | — | 0.00 |
| Structured | Fixed LZ4 | 704 | 4.94 | 207 | 4.46 / 20.74 | 256.00 |
| Structured | Demand LZ4 | 768 | 17.43 | 59 | 5.46 / 26.82 | 245.62 |
| Mixed | Off | 416 | 0.37 | 2760 | — | 0.00 |
| Mixed | Fixed LZ4 | 256 | 1.38 | 742 | 2.08 / 6.43 | 256.00 |
| Mixed | Demand LZ4 | 576 | 4.18 | 245 | 2.26 / 11.30 | 90.95 |

## What changed in the kernel

Both compressed arms use LZ4 with the same 256 MiB maximum and per-entry memcg reservations. The fixed arm allocates all 256 MiB at startup. The opt-in arm adds a pointer table to the resident control structure, obtains 16 KiB chunks with nonblocking page allocations, and frees a chunk as soon as its last entry disappears. An entry must fit within a chunk. Allocation failure releases the pending memcg reservation. The debug counters track the page-rounded control allocation plus live chunks. Kernel page tables and other VM bookkeeping also consume the shared physical budget.

## Measurement and reproduction

The six fresh boots ran off, fixed, demand, demand, fixed and off on the outer Ubuntu VMware guest. Each used the same kernel image, 768 MiB boot ceiling, two vCPUs, minimal initramfs and 64 MiB sampled headroom floor. The guest retained the 32 GiB hardware assignment. Each worker locked a quarter of its data as incompressible hot pages. Structured cold pages contained sparse records. In the mixed case, half of the cold pages contained pseudorandom bytes. Every accepted attempt read and verified every byte three times with zero disk swap I/O. Each boundary passed two additional independent confirmations on that boot. The harness checked for OOM kills and verified that entries and allocated backing returned to idle after each worker, including headroom stops.

The common-size CPU measurements cover six repetitions. CPU time includes data verification, faults, and explicit re-eviction. Latencies time the first byte load on one in 32 cold pages and use per-thread fault counts to distinguish hits. Reported percentiles are the medians of run-level percentiles. Backing after fill is a point measurement, separate from the per-boot peak. Details of the deterministic workload and the 32 MiB search grid are in the [original protocol](capacity-trial.md). Two boots per arm and a single worker limit extrapolation to a full Ubuntu application.

Run the offline check with Python 3 from the repository root:

```sh
python tools/summarize_capacity_trial.py --demand evidence/demand-01
```

The [comparison](../evidence/demand-01/comparison.json) and [six boot records and rollback](../evidence/demand-01) accompany the [workload](../experiments/capacity-trial.c) and [boot harness](../tools/capacity_trial_init.sh). To reproduce the kernel source, apply the [capacity codec patch](../patches/kernel-cram-capacity-codecs.patch) to the integrated arena first, then the [demand allocation patch](../patches/kernel-cram-demand.patch). Select the demand arm with `zswap.cram_demand=1` alongside `zswap.enabled=1 zswap.cram_arena_mb=256`. The test used `cram.series=demand-01` and a separately marked scratch volume. After the trial the VM returned to its deployed 4 MiB LZ4 arena.
