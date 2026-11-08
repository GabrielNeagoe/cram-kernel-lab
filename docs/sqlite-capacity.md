# Four-worker SQLite source search on Ubuntu

At a 320 MiB application memory budget, plain LZ4 spillover sustained 2.00× the SQLite database bytes sustained with compression off. Byte shuffle before LZ4 sustained 1.38×. The synthetic small-integer result is reported separately in the [byte-shuffle trial](byte-shuffle.md).

| Policy | Confirmed SQLite MiB | First failed SQLite MiB | Multiple of off |
|---|---:|---:|---:|
| Compression off | 271.9 | 288.9 | 1.00× |
| LZ4 spillover | 544.8 | 561.7 | 2.00× |
| Byte shuffle + LZ4 spillover | 374.0 | 390.9 | 1.38× |

At the common size, plain LZ4 used 98.8 MiB of arena backing versus 145.5 MiB with byte shuffle. The shuffle increased the space required to store these SQLite pages. Grouping bytes by word position helped the previous synthetic integer layout; here the database payload consists mainly of source text.

Each pair uses two fresh full Ubuntu boots per policy. If the values differ, the table lists both boots in order. Capacity is the largest prefix of distinct databases to pass twice per boot. The next larger prefix crossed the same sampled budget. The grid adds four databases at a time, about 17 MiB. A ratio close to 2× compares confirmed points; the underlying continuous capacity boundary can lie anywhere between the last passing and first failing sizes.

## CPU and application-page fault latency

The common workload was 204.3 MiB across 48 SQLite databases. CPU includes four workers, SQL preparation, three search sweeps, checked results, explicit re-eviction and budget sampling. Normalization uses one database image size per sweep. Throughput uses the same numerator and wall time. Loading the images and running the fault probe are outside the timed SQL interval.

| Point | Policy | CPU s/GiB | SQLite MiB/s | Fault p99 (µs) | Fault samples | Resident samples | Arena backing after fill (MiB) | Swap cache after fill (MiB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Common | Compression off | 0.69 | 4108 | — | 0 | 4,944 | 0.0 | 0.0 |
| At capacity | Compression off | 0.69 | 4115 | — | 0 | 4,356 | 0.0 | 0.0 |
| Common | LZ4 spillover | 3.72 | 435 | 14.18 | 4,944 | 0 | 98.8 | 0.0 |
| At capacity | LZ4 spillover | 4.02 | 409 | 17.79 | 8,732 | 0 | 256.0 | 5.4 |
| Common | Byte shuffle + LZ4 spillover | 5.15 | 346 | 31.69 | 4,944 | 0 | 145.5 | 0.0 |
| At capacity | Byte shuffle + LZ4 spillover | 5.39 | 334 | 42.19 | 6,012 | 0 | 256.0 | 9.7 |

A separate four-worker pass reads one byte every 64 pages at staggered offsets in the same anonymous SQLite database images. A per-thread minor or major fault increment qualifies the timed access. The p99 is the median of run-level p99s, with three common-size runs or two capacity confirmations per boot. A dash means no qualifying fault. Resident reads are counted separately. Faults can be satisfied by the Linux swap cache or by CRAM. The sampler does not attribute individual faults to either path. These timings describe a database-page access, while SQL throughput covers whole verified queries.

## Application and memory policy

The experiment ran the Ubuntu SQLite shared library on the full systemd-booted installation. The [corpus builder](../experiments/sqlite-capacity/make_corpus.py) orders tracked Linux C and header files by a hash of their paths, builds SQLite databases of distinct source files, and checks each database. The [public manifest](../evidence/sqlite-01/corpus.json) identifies the exact Linux commit, SQLite version, partition sizes and independently computed reference counts for six search terms. Four workers query disjoint partitions, scanning real B-tree records for a term and verifying row count, total source bytes and matching file count on every query. Each worker is pinned to a separate vCPU. SQLite receives a read-only anonymous database image and a 64 KiB page-cache target per connection. Images are read with direct I/O.

Both compression arms use the previously tested kernel image, two shards, 7:1 key routing, spillover and a 256 MiB total arena cap. Compression off uses no arena. The boot order was off, spillover, shuffle, shuffle, spillover, off. Host sampling observed 3 running VMware processes throughout every recorded boot trial. Across boots with two or more samples the median combined CPU use of the other VMs ranged from 1.31 to 2.34 logical cores. Available Windows RAM stayed above 42.2 GiB. Individual boot records include peak host activity. CPU scheduling and fault timings can vary under shared-host load. The VM kept its normal RAM allocation. To compare the same application budget across full boots, the harness measured the trial cgroup's entire memory.current charge, including the driver, application, page tables and resident swap cache. It subtracted the exact CRAM reservation already charged to that cgroup and added actual arena backing, including allocator overhead. An initial RSS-based pilot missed the unmapped swap cache. The final trials used fresh boots, separate worker and controller cgroups, and whole-trial charge accounting.

The workload issues MADV_PAGEOUT after loading each partition and after each timed query. A pilot that also issued forced cgroup reclaim near the boundary hit its four-minute process watchdog and was excluded. The six comparison boots use the partition hints alone. A sample over 320 MiB rejects the point immediately. The harness samples after batch completions and at 10 ms wait intervals while work is outstanding, so shorter peaks may go unobserved. The 256 MiB arena ceiling applies throughout. The workload ran inside a separate hard-limited cgroup with disk writeback disabled. Both compression policies use identical explicit page-out hints when a partition becomes cold. This tests a managed read-only search service with cold partitions. Writable databases, organic reclaim and other workloads need separate measurements.

Each accepted process checked zero disk swap I/O. The driver also checked global swap counters, cgroup OOM kills, empty arena and spill entries and return to idle backing after every process, including rejected attempts. The [rollback record](../evidence/sqlite-01/rollback.json) documents restoration of the deployed eight-vCPU 4 MiB configuration.

## Reproduction

The [trial driver](../experiments/sqlite-capacity/trial.py), [SQL workload](../experiments/sqlite-capacity/workload.py), [fault sampler](../experiments/sqlite-capacity/faults.c) and [six boot records](../evidence/sqlite-01) are public. On a disposable Ubuntu guest with the published CRAM kernel, use the Linux commit in the manifest, SQLite 3.46.1 and 4 KiB pages. In the workload directory, run:

```sh
python3 make_corpus.py /path/to/linux corpus
cc -O2 -Wall -Wextra -Werror -shared -fPIC faults.c -o faults.so
```

Boot each policy with four online vCPUs, mount debugfs, and run the driver as root inside a dedicated delegated 1500 MiB memory cgroup (systemd property `Delegate=memory`) with swap disabled for the off arm or a 1500 MiB swap ceiling for the compression arms. Use `python3 trial.py off`, `spill` or `shuffle` as appropriate. The published boot records add host VM process sample counts to the driver output. The driver validates effective kernel settings and disables cgroup writeback. Regenerate the published [comparison](../evidence/sqlite-01/comparison.json) without a VM:

```sh
python experiments/sqlite-capacity/summarize.py evidence/sqlite-01
```
