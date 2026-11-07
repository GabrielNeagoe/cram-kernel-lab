# Development plan

## Completed

- [x] Boot the patched kernel in the Ubuntu VMware guest with a stock recovery entry. Verify compression and readback in an isolated workload.
- [x] Enforce a 4 MiB arena budget covering payloads, allocation rounding, metadata, indexing and codec workspace. The fixed overhead measured 26,880 bytes. Workload and whole-VM memory accounting are separate.
- [x] Exercise concurrent reclaim, sustained churn, quota rejection and cleanup. Preserve integrity and budget outcomes for each measured case. [Kernel cases](../evidence/kernel-reservation.json) · [Outer VM cases](../evidence/vm-churn.json)
- [x] Bound VMware balloon inflation at 4 GiB, verify the real host request and compare it against a closed-balloon control.
- [x] Calibrate Windows ownership measurements against independently confirmed reuse between two processes. Track the original backing of 1,024 unlocked CRAM pages across inflation and the full hold. [Method and results](windows-frame-ownership.md)

## Next tests

1. Identify the workload index for each changed frame in the wider 8,192-page snapshot comparison. Record whether it belonged to the locked or unlocked group. Preserve a public count per group.
2. Repeat the paired run on fresh boots and report the distribution of observation gaps and owner changes. Use the same 4 GiB ceiling, matched control, workload readback and verified rollback.
3. Before claiming Windows can reuse reclaimed RAM, reproduce a transition from an original VMware-backed frame to a different Windows owner. Record the detection interval and the control result. A claim about total RAM savings also needs independent host accounting.
4. Measure CPU cost, fault latency and storage efficiency across compressible, mixed and incompressible workloads before proposing broader deployment.
