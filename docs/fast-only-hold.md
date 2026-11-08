# Physical-frame reuse with only the fast sampler during the hold

**No new frame reassignment was observed during either timed hold.** The full arm kept all 8,192 original file-offset matches throughout 10,704 scans. In the closed arm, two unlocked frames already differed at the first fast scan before the control began. Their states stayed fixed through all 10,763 confirmed hold scans.

| Confirmed hold | Balloon size | Duration | Scans | Newly observed assignments | Maximum per-frame revisit bound |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full | 2,300 MiB | 228.898 s | 10,704 | 0 | 50.075 ms |
| Closed control | 0 MiB | 233.617 s | 10,763 | 0 | 39.908 ms |

The full arm reached the requested size in 3.02 seconds with all 8,192 original frames in the guest balloon. Another 208 scans bracketing inflation saw zero free, zeroed, or differently backed frames. Both arms verified all 8,192 pages on readback with zero additional swap-disk writeback and zero OOM kills. [Paired analysis and measurements](../evidence/fast-hold-02/ownership-comparison.json)

**Control baseline carryover.** Index 728 was already zeroed and index 1704 already free at the first fast scan. Before the timed control, the sampler also recorded 29 active paged-pool observations for index 1704 (raw use code 4, page-list code 6). This shows reuse outside the timed hold, after the RAMMap baseline. The owner classifier groups pool uses under `other-or-unresolved`; the raw codes provide the pool classification. During every confirmed control scan, 728 was zeroed, 1704 was free, and the other 8,190 retained their original backing. [Windows native API header definitions](https://github.com/winsiderss/systeminformer/blob/master/phnt/include/ntmmapi.h) · [Control records](../evidence/fast-hold-02/host-pages-control/ownership.jsonl)

The preceding pair showed seven original frames being reused during a full balloon hold and one original frame changing backing during a closed control. Changes in both arms appeared late in the RAMMap hold capture. This repeat tests whether the same behavior is observable when the hold omits that capture. [Previous pair](changed-frame-investigation.md)

## Method

Both arms started on fresh boots with 32 GiB of guest RAM and a 4 MiB CRAM arena. The balloon could accept up to 4 GiB. The full arm responded to the real VMware request and the control kept the guest balloon closed. Both arms used the same 8,192 logical workload indices and the same 4,096 locked and 4,096 unlocked split as the preceding experiment. Each baseline RAMMap export supplied the physical identity of each marked VMware file offset for that boot.

Only the fast ownership sampler queried the test frames during the hold. The working-set event watcher and the additional residency scans were disabled. Independent guest counters still established balloon state, and system-level measurements still monitored safety. Host hold timers were set to 229 seconds for the full arm and 235 seconds for the closed arm, based on the preceding guest-observed hold durations. The results report measured intervals.

A reassignment is an original frame observed with different identifiable active backing. The stricter ordered count requires a free or zeroed observation followed by a later active assignment for the same frame. These are sampled observations. An event entirely between visits to a frame can escape detection, and one pair per condition provides limited evidence about frequency.

## Comparison with the previous pair

With a RAMMap hold capture, the previous pair showed seven unlocked frames reassigned under the full balloon and one under the closed control. With only fast ownership polling, neither timed hold reproduced that activity. This is consistent with the heavier observation workload contributing to the earlier result. The two pairs ran on different boots, the working-set and residency watchers also changed, and VMware requested 2,300 MiB this time versus 2,523.504 MiB previously. These runs cannot isolate a RAMMap-specific causal effect or establish net Windows RAM savings.

## Reproduce the analysis

From the repository root, run:

```sh
python tools/summarize_cram_owners.py evidence/fast-hold-02
```


The first setup attempt stopped before inflation when the verifier read an incomplete list of workload markers. The controller rolled back. The verifier now retries incomplete reads within its existing deadline. Only the subsequent fresh pair contributes measurements.

Rollback verified the original 114,485 MiB host limit, 50% minimum, restored stock balloon driver and removed startup guard. The balloon target, balloon size and offline frame count all returned to zero. The 4 MiB CRAM arena remained enabled. [Rollback record](../evidence/fast-hold-02/rollback.json)
