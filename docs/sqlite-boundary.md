# Writable SQLite at the 544.8 MiB boundary

With four workers, LZ4 spillover, demand-only swap reads and a 256 MiB arena cap, the 128-byte slot kernel sustained **544.8 MiB** on both test boots. This is **2.003×** the 271.9 MiB compression-off reference, 0.9 MiB above the 543.9 MiB doubling target. The 256-byte slot control sustained 527.7 MiB.

## Where the time went

The original kernel timed out after 240.9 seconds at 544.8 MiB. Its charge at exit was 296.6 MiB against a 320 MiB application admission budget. The worker cgroup's 280 MiB soft limit kept triggering direct reclaim. Arena backing was at its 256 MiB maximum in 46 of 48 periodic samples.

Between the first and last full samples (225.6 seconds), the workers advanced from batch 33 to 69 and scanned 32,351,072 page entries. Linux reported 207.7 seconds of full memory pressure for the cgroup (92% of elapsed time). Of 22,646,893 hot-shard spill attempts, only 67,338 succeeded (0.30%). Quota rejection, poor-compression rejection and chunk-allocation failure counters stayed at zero throughout this interval. There were 210,400 stores and 210,786 restores. 55 sampled worker stacks included both zswap store and soft-limit reclaim. A store needs a contiguous run within one chunk. With both shards nearly full, repeated attempts to place compressed entries consumed most of the pressure interval. These counters cannot separate occupied slots, rounding waste and free runs too short for a particular request. [Time series and stack samples](../evidence/sqlite-boundary-01/trace1.json)

With 128-byte slots, a separate run of the same diagnostic completed all 128 databases in 9.2 seconds and scanned 984,016 pages. Arena backing after fill was 243.0 MiB. At the 6.0-second sample, arena backing was 252.7 MiB and both spill attempts and successful spill stores were approximately 167,000. The driver reads counters individually while workers continue to run. The five-second sampling interval covers only a few instants of this shorter run. [Candidate trace](../evidence/sqlite-boundary-01/smalltrace.json)

## Allocation change

The trial patch halves the unit used to round each entry and its header from 256 to 128 bytes. It also halves the backing allocation from 16 to 8 KiB to retain 64 slots per bitmap word and preserve the fit-index algorithm. A smaller slot can reduce per-entry rounding by 128 bytes. Smaller chunks change fragmentation and increase metadata costs. The paired test measures the combination. Both use the same LZ4 codec, two shards, 7:1 routing, spillover and kernel reclaim policy. [Kernel patch](../patches/kernel-cram-128-byte-slots.patch)

At the matched 527.7 MiB fill, median rounded reservation per live entry was 1894.2 bytes with 256-byte slots and 1830.5 bytes with 128-byte slots. Median physical backing fell from 241.2 to 228.8 MiB. Live entry counts also differed (132,252 and 128,548), so the physical difference includes the change in reclaim behavior.

| Boot | Confirmed MiB | Multiple of off | First failed MiB | Outcome | Seconds | Charge at exit MiB |
| --- | --- | --- | --- | --- | --- | --- |
| small1 | 544.8 | 2.003× | 561.7 | timeout | 241.0 | 298.1 |
| standard1 | 527.7 | 1.941× | 544.8 | timeout | 240.7 | 295.9 |
| standard2 | 527.7 | 1.941× | 544.8 | timeout | 241.0 | 296.7 |
| small2 | 544.8 | 2.003× | 561.7 | timeout | 240.8 | 298.9 |

Capacity is the largest group that completed twice per boot, on a grid of about 17 MiB. Each run staged writable SQLite databases from the same Linux source corpus, committed and queried three sweeps, verified rollbacks, reopened every database and checked content digests. A budget exit exceeds the 320 MiB sampled admission charge, a stall lasts 45 seconds without a completed batch, and a timeout reaches 240 seconds. Two additional fresh compression-off boots repeated the 271.9 MiB denominator. Both completed two independent capacity confirmations and rejected the 288.9 MiB next point. The result matched the previous off boots. [Fresh off boot 1](../evidence/sqlite-boundary-01/off1.json) · [Fresh off boot 2](../evidence/sqlite-boundary-01/off2.json)

## CPU and page faults

| Slot | Point | Database MiB | CPU s/GiB | Fault p99 µs | MiB/s | Faults / probed reads | Arena MiB after fill |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Off | Confirmed capacity | 271.9 | 1.02 | — | 105 | 0 / 4,356 | 0.0 |
| 256 B | Matched and confirmed capacity | 527.7 | 10.08 | 661.5 | 370 | 8,132 / 8,464 | 241.2 |
| 128 B | Matched | 527.7 | 9.63 | 702.8 | 381 | 7,902 / 8,464 | 228.8 |
| 128 B | Confirmed capacity | 544.8 | 10.15 | 748.2 | 366 | 8,395 / 8,732 | 243.2 |

At 527.7 MiB, smaller slots reduced process CPU by 4.4% and raised median fault p99 by 6.2%. The individual p99 measurements overlapped (576–697 µs for the control and 611–748 µs for the candidate). At 544.8 MiB all four candidate processes completed in 9.2–9.6 seconds. Both controls timed out, leaving no post-SQL p99 for the original geometry at that size.

The matched comparison uses two independent processes per boot, four per geometry. Each capacity row uses two passes per boot at that policy’s boundary. CPU covers the three timed update-and-search sweeps and is normalized per GiB of database swept. The p99 is the median of the per-run 99th percentiles for sampled database reads that faulted after SQL. Elapsed SQL time, reads from resident pages and CPU seconds measure different costs. Failed attempts do not produce post-SQL fault measurements. [Validated comparison](../evidence/sqlite-boundary-01/comparison.json)

## Isolation, rollback and reproduction

The eight-vCPU deployed configuration was restored with the 4 MiB arena, normal swap, original swap readahead and stock recovery kernel. The measured boots ran only the Ubuntu VM with four pinned workers and four vCPUs. The 320 MiB sampled trial charge includes resident swap cache and actual physical arena backing. The arena ceiling covers metadata and allocator backing. Checks after each process included global disk swap I/O, cgroup OOM kills, zero remaining entries and a return to idle backing. The diagnostic sampled counters and worker stacks at roughly five-second intervals. All timing boots ran with sampling disabled. [Rollback](../evidence/sqlite-boundary-01/rollback.json) · [Build and workload hashes](../evidence/sqlite-boundary-01/provenance.json)

Use the [previous writable SQLite setup](sqlite-reclaim.md) and the identical workload, corpus, reference and compiled fault sampler. Build a second kernel with the slot patch applied to the spillover arena source. Boot each kernel with the same four-vCPU parameters and 256 MiB demand-backed LZ4 arena. Place the [driver and wrapper](../experiments/sqlite-boundary) alongside the workload and corpus. From a dedicated delegated cgroup, run `sh run.sh` for the untraced capacity search or `CRAM_DIAGNOSTIC=1 sh run.sh` for the 544.8 MiB trace. For each off control, put the previous writable reclaim driver beside the workload as `off_reference.py`, boot with zswap disabled, set the cgroup swap limit to zero, and run `sh baseline.sh`. The wrapper restores swap readahead after each run. From a repository checkout, use the following command to regenerate the summary.

```sh
python experiments/sqlite-boundary/summarize.py evidence/sqlite-boundary-01
```
