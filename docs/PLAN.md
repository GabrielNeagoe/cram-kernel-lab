# Development plan

## Completed

- [x] Boot the patched kernel in the Ubuntu VMware guest with a stock recovery entry. Verify compression and readback in an isolated workload.
- [x] Enforce a 4 MiB arena budget covering payloads, allocation rounding, metadata, indexing and codec workspace. The fixed overhead measured 26,880 bytes. Workload and whole-VM memory accounting are separate.
- [x] Exercise concurrent reclaim, sustained churn, quota rejection and cleanup. Preserve integrity and budget outcomes for each measured case. [Kernel cases](../evidence/kernel-reservation.json) · [Outer VM cases](../evidence/vm-churn.json)
- [x] Bound VMware balloon inflation at 4 GiB, verify the real host request and compare it against a closed-balloon control.
- [x] Calibrate Windows ownership measurements against independently confirmed reuse between two processes. Track the original backing of 1,024 unlocked CRAM pages across inflation and the full hold. [Method and results](windows-frame-ownership.md)

- [x] Instrument the full 8,192-page cohort by logical index. Observe release and reassignment of seven unlocked frames under a full balloon and an unlocked frame changing backing under the closed control. [Indexed repeat](changed-frame-investigation.md)

- [x] Repeat both holds with only the fast frame sampler. The tested pair had zero newly observed frame assignments during either timed hold. [Fast-only repeat](fast-only-hold.md)

- [x] Compare compression off, default LZ4 and opt-in Zstd-1 at fixed guest RAM, with disk swap excluded, verified live capacity, CPU cost and fault latency. [Capacity trial](capacity-trial.md)

- [x] Compare demand-sized and fixed LZ4 backing with compression off on fresh VMware boots. Measure capacity, CPU, first-read faults and release of empty chunks. [Demand-sized arena](demand-arena.md)

- [x] Profile matched compressed occupancy, test a chunk fit index, and recheck CPU, fault latency and capacity on fresh boots. [Slot-search profile](slot-search-profile.md)

- [x] Measure lookup chain lengths and contended acquisitions with parallel faults. Test a larger hash table and recheck parallel capacity, CPU, and fault latency. [Parallel hash profile](parallel-hash-profile.md)

- [x] Test two independently locked wide-hash shards against one arena lock under parallel faults. Recheck capacity, CPU, lock wait, and p99 latency. [Sharded arena](sharded-arena.md)

- [x] Compare balanced and deliberately skewed routing with two and four pinned workers, sample peer fit on a full rejection, and recheck four-worker capacity. [Uneven shard trial](uneven-shards.md)

- [x] Test peer placement under hot-shard saturation and recheck four-worker capacity, CPU, lock contention, and fault latency. [Spillover](spillover.md)

## Next tests

1. Alternate the presence of the RAMMap hold export over repeated fresh-boot pairs while keeping other watchers fixed. Record actual host requests and mark any ownership differences already present before the hold.
2. Repeat the paired run on fresh boots and report the distribution of observation gaps and owner changes. Use the same 4 GiB ceiling, matched control, workload readback and verified rollback.
3. Quantify net Windows RAM savings independently of individual frame reuse. Compare host-wide accounting across repeated trials and report variability.
4. Confirm any 2× capacity gain with a representative application on the full Ubuntu boot before changing the deployed 4 MiB configuration.
