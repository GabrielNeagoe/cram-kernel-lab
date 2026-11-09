# Why writable SQLite stalls at 527.7 MiB

Disabling speculative swap readahead let LZ4 spillover sustain **527.7 MiB of writable SQLite databases on both boots**, up from 510.8 MiB with normal readahead. That is **1.941×** the 271.9 MiB compression-off baseline, 16.2 MiB short of doubling. All six demand-only runs at 527.7 MiB finished in 8.5–8.9 seconds. At 544.8 MiB both demand-only boots hit the four-minute limit below the memory budget. A separate kernel change to the reclaim retry rule left capacity at 510.8 MiB.

## What reclaim scanned

At 527.7 MiB the four SQLite workers finished staging all 124 writable database images. In the follow-up trace they stalled in the second of three SQL sweeps, after 83 completed four-partition batches. The same batch was still outstanding at both sampling windows. Its final sampled memory charge was 303.2 MiB against a 320 MiB workload budget.

| Stall sample | Inactive anonymous page scans | Inactive file page scans | CRAM stores / restores | Peer spills accepted / tried |
|---|---:|---:|---:|---:|
| Original kernel, first window | 20,697,491 | 950 | 2,860 / 2,850 | 1,822 / 1,822 |
| Test kernel, change disabled, first window | 28,573,151 | 709 | 2,582 / 2,616 | 1,852 / 1,852 |
| Same batch, second window | 30,304,237 | 477 | 2,112 / 2,199 | 1,370 / 1,370 |

Each window lasted about three seconds. On the first follow-up window, the shrink trace counted 2,717 anonymous pages reclaimed. Its 2,688 calls to swap writeout used just 855 distinct folio pointer values, 641 of them repeatedly. In the second window the shrink trace counted 30,302,562 anonymous-page reactivations and 10 pages kept by the separate reference-check outcome. The array of SQLite database images occupies private anonymous memory. Worker-scoped traces also include the SQLite and Python heaps, so these counts cannot identify a specific database row. Folio identity here means a pointer within the sample and may span reuse. Arena counters are guest-wide deltas. Histogram triggers start and pause sequentially, so the event totals have slightly different boundaries. [Original trace](../evidence/sqlite-reclaim-01/trace1.json) · [Repeat trace](../evidence/sqlite-reclaim-01/trace2.json)

A separate boot sampled the kernel reference check for individual folios at stalled batch 93. In 1.10 seconds the workers visited 10,170 distinct folio identities **6,621,056 times**. One identity appeared 677 times. Of 6,649,379 reference-check returns, only 553 found a referenced page-table entry. The worker cgroup had 38.7 MiB of resident swap cache at the start of that window and 80 KiB reported as anonymous memory. No swap allocation failed, no cgroup swap-limit event fired, and the anonymous shrink trace recorded zero writeback and zero unmap failures. Worker stacks directly placed reclaim in the page-fault return path through `reclaim_high` and `evict_folios`. All four probes reported zero misses. These observations identify a small, repeatedly scanned population consistent in size with the resident swap cache. The traces did not classify every folio by mapping or record the exact branch that reactivated it. [Reference checks, residency and stacks](../evidence/sqlite-reclaim-01/trace3.json)

A fourth boot traced the decision inside `pageout()` directly. In 1.04 seconds, **3,427,691 of 3,427,859** dirty, non-shmem candidates reaching the mapping check had the anonymous-mapping bit clear. This branch returns `PAGE_ACTIVATE` before writing the folio out. The overlapping shrink histogram recorded 3,576,161 anonymous-LRU page reactivations. The probe verified the running kernel build ID and instruction sequence and reported zero misses. A folio can be on the anonymous LRU with that mapping bit clear. CRAM restores mark swap-cache pages dirty and remove their compressed copy. Speculative restores that have yet to be mapped are therefore a plausible source of these repeated activations. Individual folios were not tracked all the way from readahead to the branch. This diagnostic process subsequently exceeded the sampled workload budget while instrumentation was present and contributes no capacity or timing data. [Pageout branch counts](../evidence/sqlite-reclaim-01/trace4.json) · [Branch observer](../experiments/sqlite-reclaim/trace_pageout.py) · [CRAM restore implementation](../experiments/cram-arena.inc)


## Kernel change and capacity

The experimental switch checks whether a direct reclaim pass dropped the allocating cgroup’s charged memory by at least 32 base pages (128 KiB on this guest). When the group still exceeds its own soft limit, has a CRAM reservation, and already qualifies for a substantial throttle delay, a smaller net drop sends the worker to Linux’s existing proportional delay. The ordinary retry test credits any gross reclaim, including stores another worker immediately offsets by faulting pages back in. Net charge includes all four concurrent workers. The counter records decisions to take the backoff path. The switch defaults off. [Kernel patch](../patches/kernel-cram-high-net.patch)

The second change used the [guest swap-readahead setting](../experiments/sqlite-reclaim/readahead.sh). On two separate boots with the stock retry policy, the wrapper temporarily set `vm.page-cluster=0` before the unmodified SQL workload. This requests one swap page per demand fault. The default exponent of 3 allows read-around up to eight pages. The wrapper restored 3 on exit and the host controller verified the readback. Compression off has no swap in its workload cgroup.

| Policy | Boot | Confirmed MiB | Multiple of off | Next MiB | Outcome | Sampled MiB at exit | Page scans | Backoffs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Compression off | 1 | 271.9 | 1.000× | 288.9 | budget | 320.2 | 5,463 | 0 |
| Compression off | 2 | 271.9 | 1.000× | 288.9 | budget | 320.5 | 4,607 | 0 |
| LZ4 spillover | 1 | 510.8 | 1.878× | 527.7 | stalled | 303.2 | 796,573,656 | 0 |
| LZ4 spillover | 2 | 510.8 | 1.878× | 527.7 | stalled | 302.8 | 717,613,404 | 0 |
| LZ4 + net-charge check | 1 | 510.8 | 1.878× | 527.7 | timeout | 309.3 | 377,977,883 | 6,597 |
| LZ4 + net-charge check | 2 | 510.8 | 1.878× | 527.7 | stalled | 308.0 | 312,164,080 | 5,334 |
| LZ4, demand-only swap reads | 1 | 527.7 | 1.941× | 544.8 | timeout | 295.7 | 34,548,648 | 0 |
| LZ4, demand-only swap reads | 2 | 527.7 | 1.941× | 544.8 | timeout | 296.7 | 33,941,347 | 0 |

The driver required two fresh processes to pass at the reported boundary on each boot. Grid spacing was four databases, about 17 MiB. A stall means 45 seconds without a completed batch, a timeout means 240 seconds total, and a budget exit means a sample exceeded 320 MiB. These limits define sustainable capacity for this test. The cap on physical arena backing, including overhead, was 256 MiB in every LZ4 run.

| Policy | Boot | SQLite MiB | Outcome | Completed batches at kill | Attempt seconds | Page scans | Backoffs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LZ4 spillover | 1 | 527.7 | stalled | 92 | 92.2 | 796,573,656 | 0 |
| LZ4 spillover | 1 | 544.8 | stalled | 40 | 106.5 | 27,919,227 | 0 |
| LZ4 spillover | 2 | 527.7 | stalled | 86 | 83.2 | 717,613,404 | 0 |
| LZ4 spillover | 2 | 544.8 | stalled | 41 | 96.7 | 24,229,189 | 0 |
| LZ4 + net-charge check | 1 | 527.7 | timeout | 90 | 240.4 | 377,977,883 | 6,597 |
| LZ4 + net-charge check | 1 | 544.8 | stalled | 47 | 177.0 | 57,064,746 | 1,659 |
| LZ4 + net-charge check | 2 | 527.7 | stalled | 91 | 181.3 | 312,164,080 | 5,334 |
| LZ4 + net-charge check | 2 | 544.8 | stalled | 46 | 146.3 | 55,543,030 | 1,538 |
| LZ4, demand-only swap reads | 1 | 527.7 | pass | — | 8.7 | 908,464 | 0 |
| LZ4, demand-only swap reads | 1 | 544.8 | timeout | 70 | 240.8 | 34,548,648 | 0 |
| LZ4, demand-only swap reads | 2 | 527.7 | pass | — | 8.5 | 901,637 | 0 |
| LZ4, demand-only swap reads | 2 | 544.8 | timeout | 71 | 240.8 | 33,941,347 | 0 |

## CPU and fault latency

| Point | Policy | SQLite MiB | CPU s/GiB | SQLite MiB/s | Fault p99 µs | Faults / sampled reads | Page scans |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Common | Compression off | 254.9 | 0.86 | 2786 | — | 0 / 6,132 | 312 |
| Common | LZ4 spillover | 254.9 | 0.89 | 2700 | 10.4 | 10 / 6,132 | 1,916 |
| Common | LZ4 + net-charge check | 254.9 | 0.92 | 2605 | — | 0 / 6,132 | 21,948 |
| Common | LZ4, demand-only swap reads | 254.9 | 0.94 | 2501 | — | 0 / 6,132 | 22,196 |
| Off capacity | Compression off | 271.9 | 1.06 | 93 | — | 0 / 6,534 | 6,880 |
| Off capacity | LZ4 spillover | 271.9 | 3.36 | 877 | 876.3 | 662 / 6,534 | 195,913 |
| Off capacity | LZ4 + net-charge check | 271.9 | 3.12 | 959 | 22.8 | 606 / 6,534 | 201,244 |
| Off capacity | LZ4, demand-only swap reads | 271.9 | 3.08 | 910 | 18.0 | 490 / 6,534 | 198,328 |
| Matched pressure | LZ4 spillover | 510.8 | 9.80 | 377 | 656.2 | 11,871 / 12,294 | 1,135,408 |
| Matched pressure | LZ4 + net-charge check | 510.8 | 9.78 | 375 | 704.7 | 11,907 / 12,294 | 1,171,644 |
| Matched pressure | LZ4, demand-only swap reads | 510.8 | 9.41 | 393 | 144.3 | 11,444 / 12,294 | 835,087 |
| Policy capacity | Compression off | 271.9 | 1.02 | 97 | — | 0 / 4,356 | 6,618 |
| Policy capacity | LZ4 spillover | 510.8 | 9.95 | 373 | 664.1 | 7,914 / 8,196 | 1,126,068 |
| Policy capacity | LZ4 + net-charge check | 510.8 | 9.90 | 375 | 615.9 | 7,925 / 8,196 | 1,146,904 |
| Policy capacity | LZ4, demand-only swap reads | 527.7 | 10.11 | 369 | 879.6 | 8,134 / 8,464 | 909,464 |

CPU is process seconds per GiB swept across three write-and-search passes. Throughput uses wall time. Page-fault latency is measured after SQL by four pinned threads reading staggered database pages. Fault counts come from per-thread counters. The p99 column is the median run-level p99 among runs with a sampled fault. A dash means zero faults observed. Six processes contributed at each matched point when all passed, and four at each policy’s confirmed capacity. Page scans cover the whole process and count repeated visits. [Raw comparison](../evidence/sqlite-reclaim-01/comparison.json)

At 527.7 MiB, the net-charge retry change reduced total page scans by 52.5% and 56.5% in the two boot pairs. Each attempt ran longer before exiting. The stock retry policy finished 92 and 86 batches, compared with 90 and 91 under the change. Both retry policies confirmed the same capacity. At the first grid point above twice baseline, neither policy completed.

At the matched 510.8 MiB point, CPU was 9.80 versus 9.78 s/GiB and throughput was 377 versus 375 MiB/s. Sampled fault p99 was 656 versus 705 µs. The run-level ranges overlapped (452–867 versus 657–765 µs), and the new code registered zero backoffs in all six successful matched runs. The measurements do not establish a latency effect from backoff at this size. Costs for failed attempts are represented by elapsed time and scans. Those processes did not reach the post-SQL fault probe.

With demand-only swap reads at the same 510.8 MiB, the six-run median was 9.41 CPU s/GiB, 393 MiB/s and 144.3 µs fault p99. Median scans fell from 1,135,408 to 835,087. Across the four capacity confirmations the corresponding CPU and fault p99 were 10.11 s/GiB and 879.6 µs.

## Controls and reproduction

Ubuntu 26.04.1 LTS was the sole running VM across all measurement boots. The first six fresh boots ran in the order off, spillover, net-charge check, net-charge check, spillover, off. Two further LZ4 boots tested demand-only swap reads after the reference trace. This follow-up was run consecutively, so small timing differences may include changes in host load over time. All eight timing boots used the same kernel image, workload and source corpus. The experiment retained four pinned workers, four online vCPUs, a 280 MiB worker memory.high and the 320 MiB sampled application budget. The budget replaces the cgroup’s charge for compressed slots with actual arena backing and retains resident swap-cache costs. The separate hard cgroup limit was 1,500 MiB. Workers issued no forced page-out or reclaim requests. Every accepted run verified queries, commits, rollbacks and the reopened database contents. The driver checked for disk swap I/O and OOM kills after each attempt and verified that the arena returned to idle. The deployed eight-vCPU, 4 MiB arena configuration was restored. [Boot records](../evidence/sqlite-reclaim-01) · [Build and source hashes](../evidence/sqlite-reclaim-01/provenance.json) · [Rollback](../evidence/sqlite-reclaim-01/rollback.json)

To reproduce on the prepared disposable guest, follow the [writable SQLite setup](sqlite-writable.md), apply the kernel patch to the same two-shard spillover source tree, and place the [trial driver and trace helpers](../experiments/sqlite-reclaim) beside the unchanged workload, corpus, reference and fault sampler. Within a fresh delegated cgroup on each boot run `python3 trial.py off` with zswap disabled, or `python3 trial.py spill` with the 256 MiB demand arena, indexed wide hash, two shards, skew and spillover enabled. For the candidate add `memcontrol.cram_high_net=1` to the boot command and set `CRAM_HIGH_NET=1` in the driver environment. On separate spillover boots use `CRAM_TRACE=1`, `CRAM_TRACE=paths`, or `CRAM_TRACE=pageout` with `CRAM_VMLINUX` pointing to the exact unstripped trial vmlinux to trace the 527.7 MiB point. These diagnostics need root access to tracefs on x86-64. The original-kernel diagnostic preceded the switch and its sysfs counters. The follow-up diagnostics use the published driver on the rebuilt kernel with the switch disabled. Run `sh readahead.sh` inside the same fresh delegated cgroup on an LZ4 boot to reproduce demand-only swap reads. Regenerate the untraced comparison from the repository root with the following command.

```sh
python experiments/sqlite-reclaim/summarize_reclaim.py evidence/sqlite-reclaim-01
```
