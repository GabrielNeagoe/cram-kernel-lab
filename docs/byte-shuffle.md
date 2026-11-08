# Byte shuffle before LZ4 under skewed spillover

Ordinary spillover used 1,792 bytes of rounded arena slots per compressed page at 512 MiB structured. Byte shuffle reduced that to 1,280 bytes for the same 98,304 pages. Arena backing after fill fell from 171.5 to 128.9 MiB. At the first failed point, the original policy had accepted every attempted spill. The combination of resident locked pages, arena backing and other guest memory exhausted the headroom budget before the peer ran out of slots.

## Confirmed usable capacity

| Workload | Policy | Confirmed MiB | Next failed MiB | Multiple of compression off | CPU s/GiB at capacity | Fault p99 (µs) |
|---|---|---:|---:|---:|---:|---:|
| Structured | Compression off | 384 | 416 | 1.0 | 0.36 | — |
| Structured | LZ4 spillover | 704 | 736 | 1.833 | 5.36 | 50.44 |
| Structured | Byte shuffle + LZ4 spillover | 832 | 864 | 2.167 | 5.76 | 50.45 |
| Mixed | Compression off | 384 | 416 | 1.0 | 0.40 | — |
| Mixed | LZ4 spillover | 512 | 544 | 1.333 | 3.10 | 29.28 |
| Mixed | Byte shuffle + LZ4 spillover | 544 | 576 | 1.417 | 3.59 | 34.10 |

## Why the next point failed

The 768 MiB boot ceiling yielded about 598 MiB of Linux MemTotal. Capacity is measured relative to the live working set that passes with compression off. The harness samples Linux MemAvailable during the initial fill and between groups of blocks in the fault sweeps. The first sample below 64 MiB ends the attempt. Each row below is one boot at the first failing 32 MiB grid point. Filled MiB counts completed 1 MiB blocks. Backing and slot usage are snapshots when the floor trips. Poor compression counts unsuccessful compression operations, which can include retries. Spill counters cover the whole attempt. Every row had zero quota rejections.

| Workload | Policy and boot | Requested MiB | Filled MiB | Available MiB | Arena backing MiB | Occupied slots MiB | Poor compression | Spills stored / tried |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Structured | LZ4 spillover 1 | 736 | 723 | 64.0 | 241.7 | 237.1 | 0 | 47,881 / 47,881 |
| Structured | LZ4 spillover 2 | 736 | 736 | 62.5 | 246.1 | 241.4 | 0 | 50,292 / 50,292 |
| Structured | Byte shuffle + LZ4 spillover 1 | 864 | 854 | 63.7 | 214.2 | 200.0 | 0 | 45,335 / 45,335 |
| Structured | Byte shuffle + LZ4 spillover 2 | 864 | 862 | 62.8 | 216.2 | 201.9 | 0 | 46,679 / 46,679 |
| Mixed | LZ4 spillover 1 | 544 | 544 | 63.1 | 91.5 | 89.1 | 53,248 | 0 / 0 |
| Mixed | LZ4 spillover 2 | 544 | 544 | 63.1 | 91.0 | 88.6 | 55,040 | 0 / 0 |
| Mixed | Byte shuffle + LZ4 spillover 1 | 576 | 576 | 62.5 | 72.8 | 67.4 | 55,296 | 0 / 0 |
| Mixed | Byte shuffle + LZ4 spillover 2 | 576 | 571 | 63.9 | 72.2 | 66.9 | 54,784 | 0 / 0 |

A quarter of every workload consists of locked random bytes. Another three eighths of the mixed workload consists of evictable random bytes. With writeback disabled these incompressible pages consume guest RAM. Only three eighths of the mixed workload has the structured pattern. In an idealized payload-only calculation, even making that fraction free would leave five eighths resident, or a 1.6× ceiling for the same payload budget. The estimate excludes guest overhead and the benchmark's headroom and grid rules. It explains why improving the compressed representation alone has limited leverage on this mixed workload.

## Four-worker cost at equal working-set sizes

| Workload and MiB | Policy | Backing after fill (MiB) | CPU s/GiB | Fault p99 (µs) | Contended acquisitions | Lock wait s/GiB | Codec s/GiB |
|---|---|---:|---:|---:|---:|---:|---:|
| structured-384 | LZ4 spillover | 128.9 | 4.92 | 46.60 | 84.4% | 2.99 | 1.20 |
| structured-384 | Byte shuffle + LZ4 spillover | 96.9 | 5.31 | 46.01 | 85.2% | 3.23 | 1.32 |
| structured-512 | LZ4 spillover | 171.5 | 5.12 | 48.87 | 71.8% | 3.19 | 1.20 |
| structured-512 | Byte shuffle + LZ4 spillover | 128.9 | 5.37 | 45.69 | 85.5% | 3.33 | 1.32 |
| mixed-384 | LZ4 spillover | 64.9 | 3.04 | 28.54 | 78.7% | 1.64 | 0.75 |
| mixed-384 | Byte shuffle + LZ4 spillover | 48.9 | 3.52 | 32.45 | 82.7% | 1.94 | 0.90 |

Process CPU and p99 are medians of six uninstrumented runs per cell, three per boot. Fault p99 is the median run-level p99 of sampled faulting first-byte reads. A separate instrumented run per boot supplies contention, summed lock wait and codec time per verified GiB. Codec time includes the shuffle and inverse when enabled. Each worker checks the data and re-evicts it for three sweeps. A compressed entry placed in the peer can require two lock acquisitions. Changes in placement affect the acquisition denominator.

## Implementation and scope

The [experimental patch](../patches/kernel-cram-shuffle.patch) applies after the [spillover patch](../patches/kernel-cram-spill.patch). With the read-only boot option `zswap.cram_shuffle=1`, each shard groups the four byte positions of consecutive 32-bit words into four byte streams, runs the existing LZ4 compressor, and reverses the permutation on restore. Each shard's 4 KiB scratch buffer is charged inside its 128 MiB allocation. Both arms use the same kernel and one-way spillover under 7:1 routing. The default stays plain LZ4. The structured workload contains small integer fields and zeros, making its result specific to a favorable layout.

Six outer VMware boots ran compression off, spillover, shuffle, shuffle, spillover and compression off. Each had a 768 MiB guest boot ceiling, four pinned fault workers, a 64 MiB headroom floor, and zero disk swap traffic on accepted attempts. Only the Ubuntu VMware process was observed during sampling. The harness verifies no cgroup OOM kill, zero live spill and arena entries, and return to idle backing after each attempt. Two additional processes per boot confirm each reported capacity. The [rollback record](../evidence/shuffle-01/rollback.json) records the restored eight-vCPU, 4 MiB LZ4 configuration and removal of temporary boot options.

Recompute the [comparison](../evidence/shuffle-01/comparison.json) from the [six raw records](../evidence/shuffle-01) with the [summary tool](../tools/summarize_shuffle_trial.py):

```sh
python tools/summarize_shuffle_trial.py evidence/shuffle-01
```
