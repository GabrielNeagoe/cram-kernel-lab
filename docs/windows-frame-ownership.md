# Did Windows reuse the original CRAM frames?

On November 7, 2026, the calibrated Windows observer recorded **zero ownership changes and zero free or zeroed frames among the 1,024 tracked pages** during VMware balloon inflation and the full hold. Every observation matched the original VMware memory file and offset. These 1,024 pages represent 4 MiB. Shorter transitions between observations remain possible.

| Measurement window | Duration | Scans of all 1,024 frames | Frames observed with different backing | Longest unsampled interval |
| --- | ---: | ---: | ---: | ---: |
| Inflation, including its boundaries | 6.737 s | 318 | 0 | 32.046 ms |
| Independently confirmed full hold | 250.059 s | 11,791 | 0 | 39.378 ms |
| Closed-balloon control, including boundaries | 247.741 s | 11,773 | 0 | 89.027 ms |

The guest reached its full 2,264 MiB request in 3.03 seconds under a 4 GiB cap. All 8,192 original guest frames were in the balloon at first match and immediately before deflation. The guest verified all 8,192 pages on readback in each arm, with zero additional swap-disk writeback and zero OOM kills. The control kept the balloon at zero throughout. [Ownership comparison](../evidence/frame-owners-01/ownership-comparison.json) · [Full trial](../evidence/frame-owners-01/full1/result.json) · [Closed control](../evidence/frame-owners-01/control/result.json)

## How the cohort and observer were checked

The cohort comes from 2,048 logical workload pages previously observed re-entering VMware’s Windows working set. A reproducible split held half resident through a second mapping and left the other 1,024 unlocked. Each trial started on a fresh boot. Per-page markers verified the workload offsets before RAMMap established their backing frame numbers. The fast sampler started before inflation and compared each original physical frame against its starting file identity and offset. A separate observer polled the guest balloon counters to bracket the phases.

In a two-process calibration, Windows reassigned six known physical frames and RAMMap identified both consecutive owners for all six. A faster read-only observer then identified both owners in all 1,024 checked transfers from 2,100 known reuses. An earlier Windows allocation-event observer detected zero of 925 known transfers. These calibration workloads used their own physical allocations. [RAMMap calibration](../evidence/frame-owners-01/rammap-calibration.json) · [Fast-sampler calibration](../evidence/frame-owners-01/live-calibration.json) · [Earlier event calibration](../evidence/frame-owners-01/event-calibration.json)

The wider pair of baseline-to-hold snapshots showed **one changed physical-frame identifier among 8,192 offsets in the full trial** and **zero in the control**. A changed identifier establishes that a file offset had different backing at the two observations. The snapshots did not identify a new owner of the old frame. These counts cover a wider cohort than the 1,024 tracked continuously by sampling. [Full snapshot comparison](../evidence/frame-owners-01/host-pages-full1/physical-hold.json) · [Control snapshot comparison](../evidence/frame-owners-01/host-pages-control/physical-hold.json)

## Reproducing the comparison

Run the offline command in the [README](../README.md). The public inputs contain per-scan counts and timings, logical workload indices, phase markers, and the guest counters used by the analysis. The published projection reproduced the lab comparison exactly. The largest observed interval between samples limits what can be inferred about transient reuse.

## Rollback

The observer released its temporary page locks after each arm. The host returned to its original 114,485 MiB limit and 50% minimum. The stock guest balloon driver was restored, the startup guard was removed, and the balloon target, current size and offline frame count all read zero. The experimental 4 MiB compression arena remained enabled. [Verified rollback](../evidence/frame-owners-01/rollback.json)
