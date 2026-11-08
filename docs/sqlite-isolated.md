# SQLite search with Ubuntu as the only running VM

Four workers scanned the same Linux source corpus as in the [shared-host SQLite comparison](sqlite-capacity.md). At a 320 MiB application budget and a 256 MiB arena cap, LZ4 spillover reached 2.003× compression-off capacity. Byte shuffle before LZ4 reached 1.375×.

| Policy | Shared confirmed MiB | Isolated confirmed MiB | Isolated first failed MiB | Isolated multiple of off |
|---|---:|---:|---:|---:|
| Compression off | 271.9 | 271.9 | 288.9 | 1.000× |
| LZ4 spillover | 544.8 | 544.8 | 561.7 | 2.003× |
| Byte shuffle + LZ4 | 374.0 | 374.0 | 390.9 | 1.375× |

The search adds four database partitions per step, approximately 17 MiB. Two independent confirmations per boot must pass the SQL integrity checks and the sampled whole-cgroup budget, which includes resident swap cache and physical arena backing. The ratios compare the largest confirmed points. They do not resolve the capacity boundary between adjacent steps.

## Cost at 204.3 MiB of SQLite data

| Policy | Shared CPU s/GiB | Isolated CPU s/GiB | Shared fault p99 (µs) | Isolated fault p99 (µs) | Shared MiB/s | Isolated MiB/s |
|---|---:|---:|---:|---:|---:|---:|
| Compression off | 0.69 | 0.66 | — | — | 4108 | 4281 |
| LZ4 spillover | 3.72 | 3.62 | 14.18 | 13.79 | 435 | 451 |
| Byte shuffle + LZ4 | 5.15 | 4.89 | 31.69 | 29.34 | 346 | 366 |

The CPU and throughput figures cover three verified SQL scans with explicit page-out after use. A separate pass times first-byte reads of SQLite pages whose access incurred a thread fault. The table uses the median run-level p99 from six runs per policy. A dash means no sampled access faulted. These measurements characterize read-only partitioned search with managed page-out.

## Between-boot variation

| Policy and boot | Isolated CPU s/GiB | Shared fault p99 (µs) | Isolated fault p99 (µs) | Isolated host CPU busy (median) | Isolated available host GiB (minimum) |
|---|---:|---:|---:|---:|---:|
| Compression off 1 | 0.67 | — | — | 9.8% | 83.6 |
| Compression off 2 | 0.65 | — | — | 12.3% | 83.6 |
| LZ4 spillover 1 | 3.68 | 16.26 | 18.36 | 13.0% | 83.6 |
| LZ4 spillover 2 | 3.57 | 12.05 | 11.94 | 12.3% | 83.6 |
| Byte shuffle + LZ4 1 | 4.96 | 31.07 | 28.08 | 14.4% | 83.7 |
| Byte shuffle + LZ4 2 | 4.83 | 32.31 | 30.60 | 12.1% | 83.7 |

The host reported one VMware VM process in all 42 samples across six fresh boots. The earlier comparison had three running VMs. The host conditions were measured in successive sessions, so the timing differences can also include normal between-run variation.

Both sessions used the same kernel binary, fault sampler binary, workload scripts, corpus manifest, and boot order. Each accepted attempt verified its SQL results with zero disk swap I/O. The [rollback record](../evidence/sqlite-isolated-01/rollback.json) records the return to the deployed eight-vCPU 4 MiB LZ4 configuration. The [raw boot records](../evidence/sqlite-isolated-01) and [summary including performance at capacity](../evidence/sqlite-isolated-01/comparison.json) can be checked with:

```sh
python experiments/sqlite-capacity/summarize.py evidence/sqlite-isolated-01
```
