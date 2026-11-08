# How much live memory did CRAM add in the VMware guest?

**The best confirmed capacity was 672 MiB with Zstd-1, or 1.75× the 384 MiB compression-off baseline.** A second Zstd-1 boot confirmed 608 MiB (1.58×), the same limit reached on both LZ4 boots. Doubling the baseline would require 768 MiB. With half the cold pages incompressible, both codecs confirmed just 224 MiB (0.58× baseline). Every accepted run verified the data without disk swap traffic.

**Keep LZ4 as the default.** On structured data at the same 128 MiB working set, Zstd-1 consumed 3.86 worker CPU seconds per verified GiB versus 1.92 for LZ4. Its p99 cold-page fault took 14.76 µs versus 7.25 µs. Zstd-1 also used more CPU on mixed data with no measured capacity advantage. These CPU costs include repeated page-out and readback. Applications that revisit cold pages less often will exercise the codec differently.

The 256 MiB arena remains allocated even when a cold page cannot be compressed. The mixed result makes arena sizing the next deployment question. Compare a demand-sized arena with the fixed reservation, then verify capacity on a representative application before scaling the normal VM. Both codec arms had the same 256 MiB reservation. The fixed control structures occupied 155,904 bytes for LZ4 and 325,376 bytes for Zstd-1.

## Capacity and cost at the common working set

Capacity columns describe the search. CPU and latency columns describe the separate 128 MiB comparison. A slash in a capacity cell separates the two fresh boots where their results differed. The dash marks a run with no sampled cold-page faults.

| Workload | Mode | Verified capacity (MiB) | Next failed step (MiB) | Capacity vs off | CPU s / verified GiB | CPU vs off | Fault p50 / p99 (µs) |
|---|---|---:|---:|---:|---:|---:|---:|
| Structured | Off | 384 | 416 | 1.0× | 0.29 | 1.00× | — |
| Structured | LZ4 | 608 | 640 | 1.583× | 1.92 | 6.65× | 2.40 / 7.25 |
| Structured | Zstd-1 | 608 / 672 | 640 / 704 | 1.583 / 1.75× | 3.86 | 13.41× | 5.32 / 14.76 |
| Mixed | Off | 384 | 416 | 1.0× | 0.36 | 1.00× | — |
| Mixed | LZ4 | 224 | 256 | 0.583× | 1.29 | 3.55× | 2.03 / 5.35 |
| Mixed | Zstd-1 | 224 | 256 | 0.583× | 2.46 | 6.76× | 4.70 / 13.15 |

## Cost at each arm's own verified capacity

| Workload | Mode | CPU s / verified GiB | Verified MiB/s | Fault p50 / p99 (µs) |
|---|---|---:|---:|---:|
| Structured | Off | 0.29 | 3567.8 | — |
| Structured | LZ4 | 4.30 | 238.2 | 3.87 / 16.82 |
| Structured | Zstd-1 | 6.63 | 154.6 | 7.65 / 25.95 |
| Mixed | Off | 0.36 | 2833.8 | — |
| Mixed | LZ4 | 1.38 | 741.5 | 2.06 / 6.26 |
| Mixed | Zstd-1 | 2.56 | 399.2 | 4.69 / 13.24 |

## What counts as usable RAM

Capacity is the largest simultaneous anonymous allocation that survived three shuffled readback sweeps with all bytes correct, zero disk swap traffic and at least 64 MiB of sampled guest `MemAvailable`. Each reported limit passed twice more in fresh worker processes on that boot. An attempt at the next 32 MiB step crossed the headroom floor. In the second Zstd-1 boot, 704 MiB passed once and then crossed the floor on confirmation. The reported capacity for that boot is 672 MiB. This measures application capacity at fixed guest RAM. It does not measure Windows backing release, desktop performance, or how much a normal 32 GiB boot could hold.

The outer VMware guest booted the same patched x86-64 kernel with `mem=768M maxcpus=2` and a BusyBox initramfs. All arms shared the same physical ceiling. The compressed arms prepaid a 256 MiB CRAM arena, including the allocator bitmap, indexes and codec workspace. Page tables, swap bookkeeping and other kernel allocations also consumed guest RAM. Compression off had no arena and no active swap. The compressed arms used a dedicated swap file for slot identifiers with cgroup writeback disabled. A pass required unchanged guest `pswpin` and `pswpout`. The harness also checked for zero cgroup OOM kills and empty arena entries between workers.

Each workload locked one quarter of its pages in RAM as incompressible hot data. The structured workload filled the other three quarters with 32-byte records containing three varying 9-bit integers and twenty zero bytes. In the mixed workload, half the cold pages were replaced with deterministic pseudorandom data. Each arm requested page-out for cold blocks after fill and after each readback. Shuffling happened at the 1 MiB block level and within each block. Transparent huge pages and swap readahead were disabled. This explicit policy makes the test repeatable and gives the codec a chance to compress cold data even at the common 128 MiB size.

CPU seconds per verified GiB cover all three readback sweeps, including expected-content generation, byte comparisons, faults and re-eviction. Initial population and teardown are outside the timer. First-read latency measures the first byte load on one in 32 cold pages. Per-thread fault counters distinguish faults from resident hits. Percentiles in the table are medians of run-level percentiles. Each codec ran on two separate boots in forward and reverse order (off, LZ4, Zstd-1, Zstd-1, LZ4, off). The common-size comparison has six measurements per cell, and the at-capacity comparison has four. The results describe one VMware guest with one worker.

## Recheck the experiment

Run `python tools/summarize_capacity_trial.py evidence/capacity-trial-01` from the checkout root using Python 3 with assertions enabled. It checks the acceptance criteria and regenerates the [full comparison](../evidence/capacity-trial-01/comparison.json), including guest-wide busy CPU, resident-hit latency, sample counts, and performance at each arm's own capacity. The [per-boot records](../evidence/capacity-trial-01) preserve both accepted points and headroom stops.

The [codec patch](../patches/kernel-cram-capacity-codecs.patch) applies to `mm/cram-arena.inc` after installing the existing arena integration. Build with LZ4 and Zstd compression and decompression built in. LZ4 is the default. Zstd level 1 requires `zswap.cram_zstd1=1`. The [workload](../experiments/capacity-trial.c) builds with `gcc -O2 -Wall -Wextra -Werror -static -o bench experiments/capacity-trial.c`. The [PID 1 harness](../tools/capacity_trial_init.sh) expects a dedicated marked ext4 volume, a preallocated scratch swap file, a static BusyBox with a shell link, a working blkid at `/volid`, and a one-shot boot entry that returns to the deployed kernel. The power-on recovery default and the VM's 32 GiB configuration were preserved.

Reported measurements use the corrected trial kernel with SHA-256 `12803ac1824bf49f481beba29ccf47347bdc75a979af01958057daa19d0add36` and initramfs `e8a18546c3abaa076eba77b526293fc54757f9df3743b253627f06bdc1a8e8ca`. A preliminary series exposed a missing argument in the startup log. After fixing it, the kernel was rebuilt and all six boots were repeated. Only the corrected series contributes to the tables. The [rollback record](../evidence/capacity-trial-01/rollback.json) gives the running kernel, restored arena and CPU count after the final boot.
