"""Validate four-worker 7:1 spillover trials and regenerate the comparison."""
import argparse
import json
from pathlib import Path

from summarize_capacity_trial import compare_profile, performance, validate_boot
from summarize_hash_profile import lock_profile

COUNTERS = ("reject_full", "spill_tries", "spill_stores", "spill_loads",
            "spill_invalidates", "spill_removes")


def summarize(directory):
    result = {"capacity": {}, "workloads": {}, "backing": {}, "host_samples": {}}
    memtotals = []
    for mode in ("off", "skew", "spill"):
        boots = [json.loads((directory / f"{mode}{i}.json").read_text()) for i in (1, 2)]
        for boot in boots:
            host = boot.pop("host_samples")
            assert host["count"] > 0 and host["min_vmx"] == host["max_vmx"] == 1
            result["host_samples"].setdefault(mode, []).append(host["count"])
            validate_boot(boot, mode, cpus=4)
            memtotals.append(boot["config"]["memtotal_kib"])
            for row in boot.values():
                if row.get("status") == "pass":
                    assert row["workers"] == 4
                if mode == "off" or "status" not in row:
                    continue
                assert 0 <= row["spill_loads"] + row["spill_invalidates"] <= row["spill_removes"]
                assert row["spill_removes"] == row["spill_stores"] <= row["spill_tries"]
                if mode == "skew":
                    assert row["spill_tries"] == 0
            if mode != "off":
                backing = boot["backing"]
                assert backing["idle_bytes"] == boot["config"]["arena_overhead_bytes"]
                assert backing["idle_bytes"] <= backing["peak_bytes"] <= 256 * 2**20
                result["backing"].setdefault(mode, []).append(backing)
        capacity = result["capacity"]
        capacity[mode] = {}
        for profile in ("structured", "mixed"):
            c = compare_profile(boots, profile, mode)
            capacity[mode][profile] = c
            c["multiple_of_off"] = [round(x/y, 3) for x, y in zip(c["confirmed_capacity_mib"],
                                    capacity["off"][profile]["confirmed_capacity_mib"])]
        if mode == "off":
            continue
        for profile, size in (("structured", 384), ("structured", 512), ("mixed", 384)):
            groups = [[b[f"{profile}-load{i}-{size}"] for i in (1, 2, 3)] for b in boots]
            plain = [row for group in groups for row in group]
            probes = [b[f"{profile}-measured-{size}"] for b in boots]
            assert all(r["status"] == "pass" and r["mib"] == size for r in plain + probes)
            # A saturated shard may reject a re-eviction after its page was restored.
            assert all(r["sweep_loads"] > 0 and r["sweep_stores"] > 0 for r in plain + probes)
            for row in plain + probes:
                if not row["reject_full"]:
                    assert row["sweep_stores"] == row["sweep_loads"]
            if profile == "structured" and size == 512:
                if mode == "spill":
                    assert all(r["spill_loads"] > 0 and r["spill_invalidates"] > 0 for r in plain)
                else:
                    assert all(r["reject_full"] > 0 for r in plain)
            else:
                assert all(r["reject_full"] == r["spill_tries"] == 0 for r in plain + probes)
            result["workloads"].setdefault(f"{profile}-{size}", {})[mode] = {
                "plain": performance(plain), "plain_by_boot": [performance(rows) for rows in groups],
                "probed": performance(probes), "lock_profile": lock_profile(probes),
                "live_entries_at_fill": [r["entries_after_fill"] for r in probes],
                "occupied_bytes_at_fill": [r["used_after_fill_bytes"] for r in probes],
                "plain_counts_per_attempt": {key: [r[key] for r in plain] for key in COUNTERS},
            }
    assert max(memtotals) - min(memtotals) < 1024
    for name in ("structured-384", "mixed-384"):
        for key in ("live_entries_at_fill", "occupied_bytes_at_fill"):
            assert len({n for case in result["workloads"][name].values() for n in case[key]}) == 1
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    if not __debug__:
        parser.error("Run with assertions enabled")
    (directory / "comparison.json").write_text(json.dumps(summarize(directory), indent=2) + "\n")
