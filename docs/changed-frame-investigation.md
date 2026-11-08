# Investigating the changed physical frame

The preceding trial found one changed backing frame among 8,192 VMware file offsets between the baseline and full-balloon snapshots. Its export retained the aggregate count. The original workload index and both per-offset frame maps were discarded. The 44,990 rapid residency scans in that arm saw zero nonresident pages, so they supplied no candidate index. The historical frameâ€™s lock status and subsequent owner are unknown. [Earlier snapshot](../evidence/frame-owners-01/host-pages-full1/physical-hold.json) · [Historical residency scans](../evidence/frame-change-01/historical-residency-summary.json)

## Indexed repeat

Both fresh boots used the same logical workload and lock split. Page markers identified all 8,192 offsets before inflation. Windows locked 4,096 offsets through a separate file mapping and left 4,096 unlocked. The fast observer queried all original frames in eight batches per scan. It matched each against its original file and offset and recorded any differing metadata under its logical workload index. The scan timing includes all eight batches. Each arm also captured an independent baseline and hold snapshot. For a changed offset the exporter checks the old physical frame in the hold snapshot and saves its observed state and lock group before discarding the raw dump.

A different physical identifier at an offset is evidence that its backing changed. To establish reuse of the old frame requires a separate observation tying that old frame to another backing. A free-list observation alone would establish availability for reuse. Sampled ownership can miss changes that reverse between reads.

Recompute the paired comparison with Python 3 from the repository root.

```sh
python tools/summarize_cram_owners.py evidence/frame-change-01
```

## Full-balloon findings

The full hold showed seven original frames enter a free or zeroed state and later carry different active backing. All seven were in the 4,096-page unlocked group. Five subsequently backed process memory, one backed a different file, and one backed a different offset in the same VMware file. Workload indices are zero-based. The observer saved categories, so individual recipient process and file names are unavailable.

| Workload index | Lock status | Later active backing following an observed free or zeroed state |
| ---: | --- | --- |
| 1480 | Unlocked | Different file |
| 3859 | Unlocked | Process memory |
| 4167 | Unlocked | Different offset in the VMware file |
| 4168 | Unlocked | Process memory |
| 5169 | Unlocked | Process memory |
| 7389 | Unlocked | Process memory |
| 8174 | Unlocked | Process memory |

The guest reached the real request of 646,017 pages (2,523.504 MiB) in 1.02 seconds. The sampler observed zero changes in 220 scans bracketing inflation. It recorded 9,993 scans during 225.014 seconds of independently confirmed full hold. The largest conservative per-frame revisit bound during that hold was 150.327 ms. One of the seven pages, index 1480, was in the earlier studyâ€™s logical 1,024-page unlocked cohort. The historical changed index cannot be inferred from this overlap.

Both full-arm snapshots showed the same identifiers for all 8,192 offsets. RAMMap collects a snapshot over an extended interval. The live observer saw the seven old frames change state late in that collection. The two endpoint exports therefore undercounted reuse visible in the live record. The seven frames represent 28 KiB of observed physical reuse. Measuring net host RAM savings requires separate host-wide accounting.

[Paired comparison and per-page timing](../evidence/frame-change-01/ownership-comparison.json) Â· [Full-hold snapshot](../evidence/frame-change-01/host-pages-full1/physical-hold.json)

## Closed control and interpretation

The closed control held the balloon at zero. In 10,526 scans over 238.493 seconds, one of the 4,096 unlocked frames (index 6395) changed from original backing to active process memory. It was observed free and then zeroed later. The control recorded zero free-or-zeroed observations followed by a new active owner for that frame. Its largest per-frame revisit bound was 179.688 ms. All 4,096 locked frames retained their original backing throughout the sampled windows in both arms.

Both arms first showed ownership changes late in the RAMMap hold export, within roughly 21 seconds of its completion. Each observer run reached about 3 GiB of resident memory. The common timing and the positive closed control leave open how much ballooning, the observer’s own memory use, or background Windows activity contributed. A paired repeat using only the fast sampler during the hold would test the contribution of the hold export. Each arm passed all 8,192 page readbacks with zero additional disk writeback and zero OOM kills. [Control snapshot](../evidence/frame-change-01/host-pages-control/physical-hold.json) · [Control measurements](../evidence/frame-change-01/control/result.json)

Rollback restored the original 114,485 MiB host policy and stock balloon driver, removed the startup guard, and verified zero target, balloon size and offline frames. The 4 MiB CRAM arena remained enabled. [Rollback record](../evidence/frame-change-01/rollback.json)
