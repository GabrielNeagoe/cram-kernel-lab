# Disk-backed writable SQLite with CRAM

Four workers committed updates to disk-backed SQLite databases at both 271.9 and 544.8 MiB. **All 16 workload runs passed**, including content verification after reopening every database. Compression off handled the larger dataset within the 320 MiB sampled budget. Across all eight LZ4 runs, the arena recorded **zero stores and zero restores**, including when the per-connection pager cache target increased from 1 to 3 MiB. Disk-backed files let SQLite revisit data through storage. This experiment established a lower bound of 544.8 MiB for both configurations and did not search for their maximum database sizes.

At the larger dataset, sampled charge peaked between 289.4 and 291.2 MiB, and each run recorded over 2,100 worker memory.high events. Thus the workload encountered reclaim pressure while using no compressed entries. The previous 2.003× result applies to writable in-memory images. The timing variation here offers no evidence about the CPU cost or latency of actively compressing pages.


| Cache MiB/connection | Database MiB | Policy | Passed boots | CPU s/GiB | SQL MiB/s | Transaction p99 ms | Commit p99 ms | Checkpoint p99 ms | Stores / restores |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 271.9 | Compression off | 2/2 | 2.03 | 1060 | 32.92 | 9.99 | 6.71 | 0 / 0 |
| 1 | 271.9 | 128-byte LZ4 | 2/2 | 1.94 | 1114 | 33.42 | 9.18 | 6.53 | 0 / 0 |
| 1 | 544.8 | Compression off | 2/2 | 2.56 | 856 | 40.77 | 9.07 | 7.52 | 0 / 0 |
| 1 | 544.8 | 128-byte LZ4 | 2/2 | 2.55 | 822 | 44.54 | 24.82 | 8.32 | 0 / 0 |
| 3 | 271.9 | Compression off | 2/2 | 1.98 | 1094 | 30.09 | 8.44 | 5.63 | 0 / 0 |
| 3 | 271.9 | 128-byte LZ4 | 2/2 | 2.04 | 1020 | 35.07 | 9.59 | 8.38 | 0 / 0 |
| 3 | 544.8 | Compression off | 2/2 | 2.56 | 849 | 39.77 | 8.60 | 6.94 | 0 / 0 |
| 3 | 544.8 | 128-byte LZ4 | 2/2 | 2.57 | 849 | 40.34 | 7.92 | 8.02 | 0 / 0 |

Individual commit p99 ranges at 544.8 MiB were 1 MiB off 8.73–9.41 ms, 1 MiB LZ4 9.56–40.07 ms, 3 MiB off 7.35–9.85 ms, 3 MiB LZ4 7.50–8.33 ms. Two boots per condition give limited resolution for tail latency.

Each table entry is the median of the two boot results for that policy and size. Each boot contributed 192 transactions at 271.9 MiB and 384 at 544.8 MiB. Latencies are boot-level nearest-rank 99th percentiles, then averaged across the two boots. Transaction latency runs from BEGIN through COMMIT and includes row selection and writes. The commit column covers COMMIT alone. Checkpoint latency measures a full WAL truncate checkpoint after each transaction and query. These are guest SQLite call latencies through the VMware storage stack. SQL throughput uses logical database bytes swept in three passes. CPU includes the four writer threads during those passes. [Per-boot summary](../evidence/sqlite-disk-01/comparison.json)

## Workload and memory limits

The only running VMware VM was Ubuntu. Each of four pinned workers owned a disjoint set of database files. The test updated the first byte of every eighth eligible source row, committed under WAL with synchronous=FULL, checked a search result against the independent corpus reference, and explicitly checkpointed. SQLite used either a 1 or 3 MiB cache target per open connection and disabled mmap. The driver staged private database copies, synchronized them and requested eviction of the copied file data before each size. After the timed passes each worker rolled back another write, reopened the database, and verified every row against the expected SHA-256 digest. This checks committed readback and normal rollback. Crash recovery and writers contending on the same file require separate tests.

Boot order was off, LZ4, LZ4, off for 1 MiB, then LZ4, off, off, LZ4 for 3 MiB. Each boot ran 64 then 128 files. Both policies used a 280 MiB worker soft limit and a 320 MiB sampled application budget. The charge includes the full trial cgroup, subtracts reserved compressed slots, and adds physical arena backing. Samples ran about every 20 ms. The LZ4 boots had a 256 MiB arena cap and 128-byte slots. All boots used demand-only swap reads. The driver checked for disk swap I/O, OOM kills, arena ceiling violations and residual entries after each process. The process I/O counters in the records include connection setup and final verification. A setup pilot found the cgroup I/O controller unavailable and used no timed observations. All recorded runs used the same per-process counters. [Raw records](../evidence/sqlite-disk-01) · [Previous in-memory trial](sqlite-boundary.md)

## Reproduction

Place the [driver, workload and wrapper](../experiments/sqlite-disk) beside the same corpus and reference used by the [writable SQLite protocol](sqlite-writable.md) on the prepared experimental guest. Run the wrapper from its directory in a dedicated systemd service with memory delegation and the prior protocol's 1500 MiB outer safety limit. Use four online vCPUs, disable cgroup swap in the off arm and allow 1500 MiB of swap charge for LZ4. On the corresponding boot run `sh run.sh off` or `sh run.sh spill`. Set `SQLITE_CACHE_KIB=3072` for the larger-cache comparison. Each run cleans up its temporary database copies. The controller restored the deployed eight-vCPU, 4 MiB arena, normal swap and original swap readahead after testing. [Rollback](../evidence/sqlite-disk-01/rollback.json)

Regenerate the comparison from a checkout with the following command.

```sh
python experiments/sqlite-disk/summarize.py evidence/sqlite-disk-01
```
