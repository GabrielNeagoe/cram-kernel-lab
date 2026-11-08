# Why did demand-sized LZ4 get expensive near double capacity?

**Repeated free-slot search explained most of the extra CPU near the capacity target.** At the shared 576 MiB structured working set, all three LZ4 arms held 110,592 identical compressed entries, occupying 189.00 MiB of slots. The linear demand allocator spent 9.15 seconds per verified GiB finding space, against 0.26 for fixed LZ4. Codec time was 1.05 and 1.04 respectively.

**Indexing chunks by the longest entry they can fit cut worker CPU by 70% at 704 MiB**, from 16.54 to 4.89 seconds per verified GiB with timing probes disabled. Cold-fault p99 went from 25.45 to 18.88 µs. With probes enabled, search fell from 11.24 to 0.02 s/GiB, with 0.02 s/GiB spent maintaining the index. Hash lookup then accounted for 3.12 s/GiB. The 1,024 hash buckets held 132 entries per bucket on average immediately after fill. The benchmark measured lookup time directly. Individual chain lengths remain unmeasured.

At 192 MiB mixed, the same fix changed worker CPU from 2.17 to 1.43 s/GiB and p99 from 7.36 to 7.53 µs. Only two fresh boots contributed to each of these matched figures.

Indexed LZ4 confirmed **736 MiB structured and 544 MiB mixed**. The structured result reached 1.92× the 384 MiB compression-off baseline, one 32 MiB step short of doubling. The extra permanent index backing was 36 KiB (320 KiB total idle versus 284 KiB for linear demand). The allocator returned to its idle backing after every worker. The [differential check](../evidence/profile-01/slot-check.json) records the result of comparing indexed and linear first-fit choices independently of the VM benchmark.

## Matched workload and compressed occupancy

Each row combines two fresh boots. The worker locked a quarter of its anonymous data and explicitly evicted the cold pages after each verified sweep. At each size, the arms had identical occupied slot bytes, live compressed entries after fill, and timed sweep store counts. Occupied bytes include entry metadata and slot rounding. Physical backing differs by allocator. CPU and first-read fault percentiles below come from separate runs with the kernel timers **disabled**.

| Workload and live MiB | LZ4 allocator | Live entries | Occupied MiB | Worker CPU s / verified GiB | Cold fault p50 / p99 (µs) |
|---|---|---:|---:|---:|---:|
| structured-576 | Fixed | 110,592 | 189.00 | 4.46 | 4.05 / 20.40 |
| structured-576 | Demand, linear search | 110,592 | 189.00 | 13.84 | 4.54 / 23.21 |
| structured-576 | Demand, indexed | 110,592 | 189.00 | 4.10 | 4.00 / 15.66 |
| structured-704 | Demand, linear search | 135,168 | 231.00 | 16.54 | 5.17 / 25.45 |
| structured-704 | Demand, indexed | 135,168 | 231.00 | 4.89 | 4.61 / 18.88 |
| mixed-192 | Fixed | 18,432 | 31.50 | 1.43 | 2.13 / 7.49 |
| mixed-192 | Demand, linear search | 18,432 | 31.50 | 2.17 | 2.10 / 7.36 |
| mixed-192 | Demand, indexed | 18,432 | 31.50 | 1.43 | 2.14 / 7.53 |

## Where time went

The kernel measured elapsed time under the arena lock during three shuffled restore and re-eviction sweeps. The four timed regions are disjoint. Search locates a contiguous run of slots. Index maintenance updates the chunk’s fit information after insertions and removals. Codec covers compression and decompression including local page mapping. Hash covers entry lookups. Worker CPU also includes other kernel work and user-space data verification. Kernel elapsed times exclude time waiting for the lock and can include hypervisor scheduling delays. The profiled worker CPU is supplied for scale.

| Workload and live MiB | Allocator | Profiled CPU s/GiB | Search s/GiB | Index s/GiB | Codec s/GiB | Hash s/GiB | Search µs/store |
|---|---|---:|---:|---:|---:|---:|---:|
| structured-576 | Fixed | 4.40 | 0.264 | 0.000 | 1.042 | 2.426 | 1.345 |
| structured-576 | Demand, linear search | 13.89 | 9.152 | 0.000 | 1.050 | 2.931 | 46.552 |
| structured-576 | Demand, indexed | 4.18 | 0.017 | 0.020 | 1.021 | 2.454 | 0.087 |
| structured-704 | Demand, linear search | 16.71 | 11.240 | 0.000 | 1.057 | 3.629 | 57.172 |
| structured-704 | Demand, indexed | 4.87 | 0.018 | 0.020 | 1.029 | 3.124 | 0.091 |
| mixed-192 | Fixed | 1.45 | 0.026 | 0.000 | 0.581 | 0.180 | 0.264 |
| mixed-192 | Demand, linear search | 2.17 | 0.764 | 0.000 | 0.566 | 0.182 | 7.770 |
| mixed-192 | Demand, indexed | 1.48 | 0.005 | 0.011 | 0.580 | 0.207 | 0.052 |

## Rechecked capacity and cost at the boundary

Each capacity passed two independent confirmations per boot with timers disabled. Ratios use the corresponding compression-off boot for that workload. Slashes separate differing boots in the capacity and ratio columns. The p50 / p99 statistics summarize first-byte reads accompanied by a thread page fault. Off had no sampled cold faults.

| Workload | Allocator | Confirmed MiB | Multiple of off | Worker CPU s / verified GiB at capacity | Cold fault p50 / p99 (µs) |
|---|---|---:|---:|---:|---:|
| Structured | Off | 384 | 1.0 | 0.29 | — |
| Structured | Fixed | 672 / 608 | 1.75 / 1.583 | 5.00 | 4.46 / 23.69 |
| Structured | Demand, linear search | 736 | 1.917 | 17.57 | 5.62 / 27.53 |
| Structured | Demand, indexed | 736 | 1.917 | 5.31 | 5.05 / 21.87 |
| Mixed | Off | 416 / 384 | 1.0 | 0.37 | — |
| Mixed | Fixed | 224 | 0.538 / 0.583 | 1.47 | 2.11 / 6.84 |
| Mixed | Demand, linear search | 544 | 1.308 / 1.417 | 4.22 | 2.23 / 11.28 |
| Mixed | Demand, indexed | 544 | 1.308 / 1.417 | 1.89 | 2.22 / 9.20 |

## Implementation and checks

Linear demand allocation restarts the free-space bitmap search at slot zero and requires each entry to fit entirely in one 16 KiB chunk. Small unused tails can remain across many chunks. The opt-in index has one bitmap row per possible request length (1–17 slots on this kernel). Each row identifies chunks with a sufficiently long free run. Allocation selects the first qualifying chunk and then searches within its 64 slots. Release and insertion update that chunk’s rows under the existing lock. The index and its padding are charged inside the 256 MiB arena limit. Empty chunks still return their backing pages. The differential check compares the two kernel slot-selection functions across changing occupancy, including completely full and empty maps, partial last bitmap words and all 17 request sizes.

Eight one-shot outer VMware boots ran in forward and reverse order. All used the same image, two vCPUs, a 768 MiB physical boot ceiling and a sampled 64 MiB headroom floor. The compression-off arm used the full budget without an arena. On every accepted attempt, three complete sweeps verified the bytes with zero disk swap traffic. The harness checked for OOM kills and verified return to idle backing after each worker. The 704 MiB structured comparison includes the two demand allocators only. It exceeds the shared 576 MiB point used to compare all three LZ4 modes. The shorter 192 MiB mixed control fits within the fixed allocator’s mixed capacity. This synthetic, single-worker test cannot establish full desktop capacity or contention under parallel faults.

The [kernel patch](../patches/kernel-cram-slot-index.patch) applies after the [demand arena patch](../patches/kernel-cram-demand.patch). Enable the opt-in fix with `zswap.cram_index=1` on a demand-mode trial boot. LZ4 remains the default codec. The [raw boot records, comparison and rollback](../evidence/profile-01) and [workload](../experiments/capacity-trial.c) accompany the [boot harness](../tools/capacity_trial_init.sh). Reproduce the comparisons from the repository root:

```sh
python tools/summarize_slot_profile.py evidence/profile-01
```

To run the differential check on a 64-bit Linux build host after applying the three capacity-trial patches, supply the resulting kernel arena include:

```sh
python tools/check_slot_index.py /path/to/linux/mm/cram-arena.inc
```
