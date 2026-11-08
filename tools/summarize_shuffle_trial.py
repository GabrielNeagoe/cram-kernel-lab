"""Compare byte-shuffled LZ4 with unchanged spillover and compression off."""
import argparse
import json
from pathlib import Path

from summarize_capacity_trial import compare_profile, performance, validate_boot
from summarize_hash_profile import lock_profile


def summarize(directory):
    results = {}
    memtotals = []
    for mode in ("off", "spill", "shuffle"):
        boots = []
        for n in (1, 2):
            boot = json.loads((directory / f"{mode}{n}.json").read_text())
            samples = boot.pop("host_samples")
            assert samples["count"] and samples["min_vmx"] == samples["max_vmx"] == 1
            validate_boot(boot, mode, cpus=4)
            memtotals.append(boot["config"]["memtotal_kib"])
            for row in boot.values():
                if row.get("status") == "pass":
                    assert row["workers"] == 4
                if mode != "off" and "status" in row:
                    assert row["spill_loads"] + row["spill_invalidates"] <= row["spill_removes"]
                    assert row["spill_stores"] == row["spill_removes"] <= row["spill_tries"]
                if mode != "off" and row.get("status") == "headroom":
                    assert 0 < row["arena_used_bytes"] <= row["arena_resident_bytes"] <= 256 * 2**20
                    assert 0 < row["filled_mib"] <= row["mib"]
            if mode != "off":
                backing = boot["backing"]
                assert backing["idle_bytes"] == boot["config"]["arena_overhead_bytes"]
                assert backing["idle_bytes"] <= backing["peak_bytes"] <= 256 * 2**20
                assert backing["allocation_failures"] == 0
            boots.append(boot)
        capacity = {}
        matched = {}
        for profile in ("structured", "mixed"):
            capacity[profile] = c = compare_profile(boots, profile, mode)
            baseline = c if mode == "off" else results["off"]["capacity"][profile]
            c["multiple_of_off"] = [round(v / ref, 3) for v, ref in
                                    zip(c["confirmed_capacity_mib"], baseline["confirmed_capacity_mib"])]
            if mode == "off":
                continue
            failures = []
            for boot, upper in zip(boots, c["next_failed_mib"]):
                row = next(r for key, r in boot.items() if key.startswith(profile + "-")
                           and r.get("mib") == upper and r.get("status") == "headroom")
                failures.append({k: row[k] for k in ("mib", "filled_mib", "available_kib",
                    "arena_entries", "arena_used_bytes", "arena_resident_bytes", "reject_poor",
                    "reject_quota", "reject_full", "spill_tries", "spill_stores")})
            c["first_failed_grid_point"] = failures
            sizes = (384, 512) if profile == "structured" else (384,)
            for size in sizes:
                rows = [b[f"{profile}-load{i}-{size}"] for b in boots for i in (1, 2, 3)]
                probes = [b[f"{profile}-measured-{size}"] for b in boots]
                assert all(r["status"] == "pass" for r in rows + probes)
                matched[f"{profile}-{size}"] = {"plain": performance(rows),
                    "lock_profile": lock_profile(probes),
                    "entries_at_fill": [r["entries_after_fill"] for r in probes],
                    "slot_bytes_at_fill": [r["used_after_fill_bytes"] for r in probes]}
        results[mode] = {"capacity": capacity, "matched": matched}
    assert max(memtotals) - min(memtotals) < 1024
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if not __debug__:
        parser.error("Run with assertions enabled")
    (args.directory / "comparison.json").write_text(json.dumps(summarize(args.directory), indent=2) + "\n")
