# Writable SQLite under Linux memory pressure

Four parallel workers updated and searched SQLite databases containing real Linux source files. With LZ4 spillover the largest confirmed database footprint was 1.878× the compression-off baseline. Each policy ran twice on a full Ubuntu boot with one VMware VM running, a 320 MiB sampled application budget and four vCPUs. The compressed arm used the existing 7:1 two-shard kernel and its 256 MiB arena ceiling.

| Policy | Confirmed database MiB | First failed database MiB | Multiple of off |
|---|---:|---:|---:|
| Compression off | 271.9 | 288.9 | 1.000× |
| LZ4 spillover | 510.8 | 527.7 | 1.878× |

Two independent processes per boot had to complete at the reported capacity; the next group of four partitions, approximately 17 MiB larger, failed the admission test. Paired values, when different, are in boot order. Capacity measures live writable database image bytes. Doubling the confirmed baseline would require 543.9 MiB. Both LZ4 boots finished 33.1 MiB below that target.

| Policy and boot | Next database MiB | First failure | Sampled charge MiB | Attempt seconds | Pages scanned |
|---|---:|---|---:|---:|---:|
| Compression off 1 | 288.9 | budget | 320.4 | 24.3 | 3,701 |
| Compression off 2 | 288.9 | budget | 320.3 | 20.1 | 3,987 |
| LZ4 spillover 1 | 527.7 | stalled | 302.3 | 56.2 | 488,408,035 |
| LZ4 spillover 2 | 527.7 | stalled | 301.4 | 55.1 | 464,366,060 |

A budget exit records a sampled charge above 320 MiB. A stalled exit means no group of four partitions finished for 45 seconds. A timeout ends a process after 240 seconds total. The parent records the charge before terminating a stalled or timed-out child; this is a bound on sustainable capacity under the stated time limits. Page-scan counters count repeated scans of the same frames. The time limits also applied to the compression-off boots. A preliminary oversized run spent four minutes in reclaim at full arena occupancy, prompting separate progress and budget classifications. Preliminary runs are excluded.

At 527.7 MiB, both LZ4 attempts stalled below the sampled memory budget while Linux repeatedly scanned pages. The records establish a progress limit under this reclaim policy. They leave open whether a longer wait would complete.

## Write cost and fault latency

| Point | Policy | SQLite MiB | CPU s/GiB | SQLite MiB/s | Fault p99 (µs) | Faulting / sampled accesses | Runs with a fault |
|---|---|---:|---:|---:|---:|---:|---:|
| Common | Compression off | 254.9 | 0.89 | 2590 | — | 0 / 6,132 | 0/6 |
| Near baseline capacity | Compression off | 271.9 | 1.02 | 103 | — | 0 / 6,534 | 0/6 |
| Policy capacity | Compression off | 271.9 | 1.04 | 101 | — | 0 / 4,356 | 0/4 |
| Common | LZ4 spillover | 254.9 | 0.91 | 2551 | — | 0 / 6,132 | 0/6 |
| Near baseline capacity | LZ4 spillover | 271.9 | 3.29 | 897 | 955.06 | 595 / 6,534 | 6/6 |
| Policy capacity | LZ4 spillover | 510.8 | 9.71 | 381 | 714.41 | 7,937 / 8,196 | 4/4 |

Each of three timed sweeps commits an update to the first byte of every eighth nonempty row in each database and searches its source bodies for a term. Independent expected counts verify the search results. CPU covers worker process time, including SQLite, validation of query results and budget monitoring during the sweeps. The denominator is the sum of database image sizes scanned across the three sweeps. Kernel time spent by the workers in direct reclaim is part of process CPU. Time spent asleep under memory.high throttling affects wall throughput. At the common sizes the table reports medians of six runs across two boots. At capacity it reports four confirmation runs. The fault column is the median run-level p99 among runs with fault samples. A dash means none were observed. For a run with fewer than 100 faults, its nearest-rank p99 is its slowest sampled fault.

At the 271.9 MiB point, individual LZ4 runs sampled 94–105 faults each. Their run-level p99s ranged from 16.6 to 1,896.5 µs.

After timing, four workers sample first-byte reads at staggered 64-page intervals in the same images; per-thread fault counters distinguish faults from resident reads. Linux determines which pages remain resident, so sample counts can differ substantially. The probe measures page-access latency separately from SQL transaction latency. Next, each partition rolls back a fourth update, closes and reopens the database connection, and verifies a SHA-256 digest of every path and body against an independent reference computed from the original files. The test exercises transactional consistency in writable in-memory SQLite with an in-memory journal. Crash durability, WAL I/O and multi-writer contention on one database are outside this measurement.

## Evidence of natural reclaim

| Point | Policy | memory.high events | Pages scanned | CRAM restores during SQL | Arena backing after fill (MiB) |
|---|---|---:|---:|---:|---:|
| Common | Compression off | 18 | 384 | 0 | 0.0 |
| Near baseline capacity | Compression off | 2,148 | 7,049 | 0 | 0.0 |
| Policy capacity | Compression off | 2,190 | 7,624 | 0 | 0.0 |
| Common | LZ4 spillover | 20 | 16,061 | 7 | 0.8 |
| Near baseline capacity | LZ4 spillover | 862 | 206,070 | 52,639 | 8.6 |
| Policy capacity | LZ4 spillover | 7,392 | 1,173,302 | 413,019 | 226.5 |

The worker cgroup had memory.high set to 280 MiB in both arms. Crossing it asks Linux to reclaim memory and can throttle workers. The harness issued no page-out hints or explicit cgroup reclaim. The table shows median run-level event deltas over the entire attempt; restore counts cover the timed SQL sweeps only. A discarded 240 MiB setup pilot exhibited prolonged throttling with compression off. All published boots used 280 MiB. The separate 320 MiB admission check counted the whole trial cgroup including resident swap cache, replaced its compressed slot charge with actual arena backing, and rejected over-budget samples. Cgroup disk writeback was disabled and disk swap I/O and OOM-kill counters were checked at every attempted point. Arena entries returned to zero and backing returned to idle on the compressed boots.

The earlier [isolated read-only SQLite comparison](sqlite-isolated.md) used explicit page-out after each partition. Workload mutability and reclaim policy both changed here, so the difference between experiments cannot isolate the effect of writes alone.

## Reproduction

The [corpus manifest](../evidence/sqlite-01/corpus.json), [write expectations](../evidence/sqlite-write-01/reference.json), [boot records and build hashes](../evidence/sqlite-write-01), [workload](../experiments/sqlite-writable/workload.py), and [rollback record](../evidence/sqlite-write-01/rollback.json) accompany this report. Use the corpus builder and fault-sampler build steps from the [SQLite protocol](sqlite-capacity.md). Place the corpus and compiled faults.so beside the new workload, generate reference.json with reference.py, then run trial.py with off or spill inside the same dedicated delegated cgroup. Use four online vCPUs and keep other VMs powered off. Recompute the [summary](../evidence/sqlite-write-01/comparison.json) from the repository root:

```sh
python experiments/sqlite-writable/summarize_writes.py evidence/sqlite-write-01
```
