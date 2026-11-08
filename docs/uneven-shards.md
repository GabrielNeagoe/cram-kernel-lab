# Four-worker CRAM under uneven shard load

**The hot shard rejected compressed entries while the peer had room.** In each of the two primary 512 MiB structured runs, skewed routing caused 49,960 full rejections. Across those runs, the peer could fit the exact request in 362 of 362 successful simultaneous checks. In 345 of them the first qualifying chunk already had physical backing. At the last recorded fit on each boot, the hot shard had 128.00 MiB of backing. The peer had 21.70 MiB on both boots. All four workers completed full readback verification with zero disk swap I/O.

**Structured capacity fell from 704 MiB with balanced shards to 576 MiB under 7:1 skew.** The skewed case delivered 1.50–1.64× the paired compression-off baselines (384 and 352 MiB). Balanced shards reached 2.00× on the 352 MiB baseline and 1.83× on the 384 MiB baseline. Doubling did not repeat. Mixed capacity was 512 MiB, or 1.333× baseline. The grid resolution was 32 MiB.

Before saturation, four-worker structured contention rose from 68.5% to 84.3% at the same measured compressed occupancy. CPU rose from 3.36 to 5.03 seconds per verified GiB, and p99 fault latency changed from 26.61 to 46.46 µs. At 512 MiB with the hot shard full, contention was 69.6% balanced and 84.3% skewed. Worker CPU was 3.41 and 4.81 s/GiB, and p99 was 25.58 and 45.71 µs.

## Did the neighboring shard have room?

Both shards had an independent 128 MiB ceiling. The structured 512 MiB experiment exercised the same live allocation in both routing modes with four faulting workers. Each row covers one attempt including initial fill, verification, re-eviction, and teardown. Rejections count store attempts. A page can be retried. A dash means that attempt recorded no peer-fit observation.

| Routing and boot | Workload status | Full rejections | Sampled with both locks | Peer had an exact fit | First qualifying peer chunk already backed | Owner backing at last fit (MiB) | Peer backing at last fit (MiB) |
|---|---|---:|---:|---:|---:|---:|---:|
| Two, balanced 1 | pass | 0 | 0 | 0 | 0 | — | — |
| Two, balanced 2 | pass | 0 | 0 | 0 | 0 | — | — |
| Two, skewed 1 | pass | 49,960 | 181 | 181 | 173 | 128.00 | 21.70 |
| Two, skewed 2 | pass | 49,960 | 181 | 181 | 172 | 128.00 | 21.70 |

On the first and then every 256th shard-zero full rejection, the probe tried the peer lock once, while still holding the owner's lock. If it succeeded, it checked whether the peer's slot index could accommodate the rejected entry length in one chunk. It separately counted fits in an already-backed first qualifying chunk. The last-fit measurements record both shards at the same instant. This tests placement eligibility. It does not move a page, attempt a peer allocation, or estimate the effect of a spillover policy. Failed peer trylocks are reported separately in the [raw records](../evidence/skew-01).

## Matched performance before the hot shard filled

The structured and mixed working sets were each 384 MiB. The offline check requires the same compressed-entry count, occupied bytes after fill and completed restore/store counts across routing modes, worker counts and boots **within each workload**. CPU, throughput and p99 summarize six probe-disabled runs per cell. Contention, wait, and shard shares come from two separately instrumented runs per cell. CPU is aggregate process time. Wait measures the sum of failed immediate arena-lock acquisition intervals per verified GiB. Each run pooled fault samples across its workers before computing p99.

| Workload | Routing | Workers | Stores to shard zero (%) | Contended | Wait s/GiB | CPU s/GiB | Verified MiB/s | Fault p99 (µs) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Structured | One lock | 2 | — | 84.8% | 0.96 | 2.82 | 715 | 14.02 |
| Structured | One lock | 4 | — | 97.3% | 3.80 | 5.65 | 705 | 51.68 |
| Structured | Two, balanced | 2 | 49.996 | 32.2% | 0.29 | 2.12 | 948 | 12.42 |
| Structured | Two, balanced | 4 | 50.003 | 68.5% | 1.31 | 3.36 | 1179 | 26.61 |
| Structured | Two, skewed | 2 | 87.492 | 66.0% | 0.76 | 2.63 | 765 | 13.77 |
| Structured | Two, skewed | 4 | 87.497 | 84.3% | 3.06 | 5.03 | 791 | 46.46 |
| Mixed | One lock | 2 | — | 59.9% | 0.48 | 1.79 | 1119 | 10.64 |
| Mixed | One lock | 4 | — | 90.8% | 2.05 | 3.43 | 1143 | 32.69 |
| Mixed | Two, balanced | 2 | 50.014 | 26.8% | 0.19 | 1.50 | 1323 | 8.74 |
| Mixed | Two, balanced | 4 | 50.0 / 49.997 | 59.8% | 0.75 | 2.23 | 1756 | 17.56 |
| Mixed | Two, skewed | 2 | 87.486 / 87.503 | 46.9% | 0.38 | 1.71 | 1168 | 10.40 |
| Mixed | Two, skewed | 4 | 87.508 / 87.497 | 78.4% | 1.68 | 3.10 | 1259 | 29.18 |

## Four-worker costs with the hot shard under pressure

Four additional boots ran balanced, skewed, skewed, balanced with the same kernel and workload binary. The supplemental initramfs added repeated plain and probed runs at both live sizes. The table gives passing counts out of four attempts of each kind. The data and total live allocation match across arms. Rejected stores leave some pages resident, so compressed occupancy can differ. This table measures the combined effect of contention and rejected stores.

| Live MiB | Routing | Plain / probed passes | Compressed entries after fill | Contended | Wait s/GiB | CPU s/GiB | Verified MiB/s | Fault p99 (µs) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 480 | Two, balanced | 4 / 4 | 92,160–92,160 | 69.3% | 1.32 | 3.35 | 1185 | 26.41 |
| 512 | Two, balanced | 4 / 4 | 98,304–98,304 | 69.6% | 1.34 | 3.41 | 1165 | 25.58 |
| 480 | Two, skewed | 4 / 4 | 85,046–85,047 | 84.2% | 2.98 | 4.89 | 813 | 44.73 |
| 512 | Two, skewed | 4 / 4 | 85,814–85,815 | 84.3% | 2.94 | 4.81 | 831 | 45.71 |

## Four-worker capacity

All capacities passed two additional confirmations per boot. Next failed is the next tested 32 MiB step that crossed the sampled 64 MiB headroom floor. Ratios use the compression-off capacity on the corresponding forward or reverse boot. Slashes separate the two boot results if they differ. At the capacity boundary, throughput and fault measurements reflect each arm's own largest verified allocation. A rejected store can leave a cold page resident, so those costs answer a different question from the matched comparison.

| Workload | Routing | Confirmed MiB | Next failed MiB | Multiple of off | CPU s/GiB at capacity | Fault p99 (µs) |
|---|---|---:|---:|---:|---:|---:|
| Structured | Off | 352 / 384 | 384 / 416 | 1.0 | 0.34 | — |
| Structured | One lock | 704 | 736 | 2.0 / 1.833 | 5.71 | 50.74 |
| Structured | Two, balanced | 704 | 736 | 2.0 / 1.833 | 3.44 | 27.02 |
| Structured | Two, skewed | 576 | 608 | 1.636 / 1.5 | 4.78 | 44.20 |
| Mixed | Off | 384 | 416 | 1.0 | 0.40 | — |
| Mixed | One lock | 512 | 544 | 1.333 | 3.47 | 32.52 |
| Mixed | Two, balanced | 512 | 544 | 1.333 | 2.25 | 18.05 |
| Mixed | Two, skewed | 512 | 544 | 1.333 | 3.14 | 29.27 |

## Protocol and reproduction

Eight fresh boots of the outer VMware VM ran off, one lock, balanced shards, skewed shards, then in reverse. All had four vCPUs, a 768 MiB physical boot ceiling, the same LZ4 kernel and 256 MiB budget when compression was enabled. Each worker ran on its own vCPU, synchronized at the start of each sweep, and verified disjoint shuffled blocks. The structured workload has compressible cold records and an incompressible locked quarter. In the mixed workload, half the cold pages also contain incompressible data. Each passing attempt verified all data three times with zero disk swap I/O. The harness checked for cgroup OOM kills and release of entries and chunk backing after workers exited.

The trial-only routing option chooses shard one for one of eight hash values from bits outside the 15-bit local bucket index. The local table size, LZ4 algorithm, data, allocator and total budget remain the same. The 7:1 case is deliberately induced and reproducible. The comparison covers two and four workers on one four-vCPU guest. It does not establish a typical skew for an application. Every host sample across the 12 outer boots showed one VMware VM process, with Ubuntu running. See the [comparison](../evidence/skew-01/comparison.json) for sample counts, per-boot metrics, and witness totals including headroom stops.

Apply the [test routing and peer-fit patch](../patches/kernel-cram-skew-trial.patch) after the [two-shard patch](../patches/kernel-cram-two-shards.patch). The opt-in flag is `zswap.cram_skew_test=1` with the wide-hash two-shard LZ4 arena enabled. The [workload](../experiments/capacity-trial.c), [harness](../tools/capacity_trial_init.sh), [boot records and rollback](../evidence/skew-01) accompany this report. Reproduce the comparisons with Python 3:

```sh
python tools/summarize_hash_profile.py --skew evidence/skew-01
```
