# Development plan

## Completed

- [x] Boot the patched kernel in the Ubuntu VMware guest with a stock recovery entry. Verify compression and readback in an isolated workload.
- [x] Enforce a 4 MiB arena budget covering payloads, allocation rounding, metadata, indexing and codec workspace. The fixed overhead measured 26,880 bytes. Workload and whole-VM memory accounting are separate.
- [x] Exercise concurrent reclaim, sustained churn, quota rejection and cleanup. Preserve integrity and budget outcomes for each measured case. [Kernel cases](../evidence/kernel-reservation.json) · [Outer VM cases](../evidence/vm-churn.json)
- [x] Bound VMware balloon inflation at 4 GiB, verify the real host request and compare it against a closed-balloon control.
- [x] Calibrate Windows ownership measurements against independently confirmed reuse between two processes. Track the original backing of 1,024 unlocked CRAM pages across inflation and the full hold. [Method and results](windows-frame-ownership.md)

- [x] Instrument the full 8,192-page cohort by logical index. Observe release and reassignment of seven unlocked frames under a full balloon and an unlocked frame changing backing under the closed control. [Indexed repeat](changed-frame-investigation.md)

## Next tests

1. Repeat with the fast sampler alone during the hold. Both arms showed changes late in the RAMMap hold export. Keep the baseline mapping, matched closed control and rollback so the effect of the hold export can be isolated.
2. Repeat the paired run on fresh boots and report the distribution of observation gaps and owner changes. Use the same 4 GiB ceiling, matched control, workload readback and verified rollback.
3. Quantify net Windows RAM savings independently of individual frame reuse. Compare host-wide accounting across repeated trials and report variability.
4. Measure CPU cost, fault latency and storage efficiency across compressible, mixed and incompressible workloads before proposing broader deployment.
