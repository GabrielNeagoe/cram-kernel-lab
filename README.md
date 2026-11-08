# CRAM kernel lab

Experimental compressed memory on Ubuntu 26 in VMware Workstation. This repository contains kernel and emulator patches, C workloads, Python test tools, and measurements. The deployed software experiment uses a 4 MiB compressed arena behind Linux zswap. A separate nested QEMU experiment uses an emulated CXL device.

- [Development plan](docs/PLAN.md)
- [Peer-shard spillover under 7:1 load](docs/spillover.md)
- [Byte shuffle before LZ4 under skewed spillover](docs/byte-shuffle.md)
- [Four-worker SQLite capacity on full Ubuntu boots](docs/sqlite-capacity.md)
- [SQLite capacity and latency with Ubuntu alone](docs/sqlite-isolated.md)
- [Writable SQLite under Linux memory pressure](docs/sqlite-writable.md)
- [Uneven shard load and four-worker capacity](docs/uneven-shards.md)
- [One versus two arena locks at matched occupancy](docs/sharded-arena.md)
- [Parallel fault, hash lookup and lock contention measurements](docs/parallel-hash-profile.md)
- [Matched-occupancy CPU profile and indexed slot allocation](docs/slot-search-profile.md)
- [Demand-sized versus fixed LZ4 capacity and backing](docs/demand-arena.md)
- [VM capacity, CPU cost and fault latency for LZ4 and Zstd-1](docs/capacity-trial.md)
- [Windows physical-frame experiment](docs/windows-frame-ownership.md)
- [Indexed follow-up on all 8,192 frames](docs/changed-frame-investigation.md)
- [Fast-only hold repeat](docs/fast-only-hold.md)
- [Kernel reservation measurements](evidence/kernel-reservation.json)
- [VM churn measurements](evidence/vm-churn.json)

## Development sources

The nested experiment used [CRAM Linux at 22cb354](https://github.com/gourryinverse/linux/commit/22cb35457d24fb35765ef124f9c74c08a5f546a9) and [modified QEMU at a078aae](https://github.com/gourryinverse/qemu/commit/a078aaedd382eedd121a037ffa992bb9db964acb). The software arena is an experimental addition in this lab. Its kernel integration is in [the arena patch](patches/kernel-cram-arena-reservation.patch), with the [allocator](experiments/cram-arena.inc) and [reservation accounting](experiments/cram-memcg-reservation.inc). The VMware trials ran kernel `7.2.0-rc2-cram-vm-arena-r2+` on the outer Ubuntu VM.

## Recheck the published result

From the repository root, run the following command with Python 3. It reads the published measurements, checks the trial invariants, and regenerates the ownership comparison.

```sh
python tools/summarize_cram_owners.py evidence/frame-owners-01
```

The live trial harnesses target a prepared disposable VM and assume lab-specific build directories, a stock recovery boot entry, an isolated test cgroup, and an authenticated host observer. Some build helpers use intermediate source trees under `private/`. The repository distributes sources and measurements. The offline check above runs without the VM, RAMMap or administrator access.
