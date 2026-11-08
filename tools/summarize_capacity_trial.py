"""Recompute the capacity and performance comparisons from six VMware boot records."""
import argparse
import json
from pathlib import Path
from statistics import median

MODES = ("off", "lz4", "zstd1")
PROFILES = ("structured", "mixed")


def performance(rows):
    def cpu_per_gib(field):
        return round(median(
            r[field] / 1e9 * 1024 / r["verified_mib"] for r in rows
        ), 4)

    out = {
        "measurements": len(rows),
        "worker_cpu_s_per_gib": cpu_per_gib("worker_cpu_ns"),
        "guest_busy_s_per_gib": cpu_per_gib("guest_busy_ns"),
        "verified_mib_per_wall_s": round(median(
            r["verified_mib"] * 1e9 / r["wall_ns"] for r in rows
        ), 2),
    }
    for kind in ("fault", "hit"):
        count = f"{kind}_samples"
        observed = [r for r in rows if r[count] > 0]
        out[count] = sum(r[count] for r in rows)
        for percentile in ("p50", "p99"):
            key = f"{kind}_{percentile}"
            out[f"{key}_us"] = (
                round(median(r[f"{key}_ns"] for r in observed) / 1000, 3)
                if observed else None
            )
    out["median_restores_per_sweep"] = median(r["sweep_loads"] / r["rounds"] for r in rows)
    if all("arena_after_fill_bytes" in r for r in rows):
        out["median_arena_after_fill_bytes"] = median(r["arena_after_fill_bytes"] for r in rows)
    return out


def validate_boot(boot, mode):
    cfg = boot["config"]
    assert cfg["mode"] == mode and cfg["cpus"] == 2 and cfg["grid_mib"] == 32
    assert cfg["memory_floor_kib"] == 65536 and cfg["swap_writeback"] is False
    assert cfg["arena_bytes"] == (0 if mode == "off" else 256 * 2**20)
    assert 0 <= cfg["arena_overhead_bytes"] <= cfg["arena_bytes"]
    for key, row in boot.items():
        if key in {"config", "backing"} or key.endswith("-bound"):
            continue
        assert row["status"] in {"pass", "headroom"}
        if row["status"] == "headroom":
            assert row["available_kib"] < cfg["memory_floor_kib"]
            continue
        assert row["rounds"] == 3 and row["verified_mib"] == 3 * row["mib"]
        assert row["minimum_available_kib"] >= cfg["memory_floor_kib"]
        assert row["disk_swap_in_out"] == 0
        # Eight cold-page samples per 1 MiB cold block in each of three sweeps.
        assert row["fault_samples"] + row["hit_samples"] == (row["mib"] * 3 // 4) * 8 * 3
        if mode == "off":
            assert row["sweep_stores"] == row["sweep_loads"] == 0


def compare_profile(boots, profile, mode):
    limits, upper, common, confirmed = [], [], [], []
    for boot in boots:
        bounds = boot[f"{profile}-bound"]
        lo, hi = bounds["verified_mib"], bounds["upper_mib"]
        assert hi - lo == boot["config"]["grid_mib"]
        assert any(
            row.get("mib") == hi and row.get("status") == "headroom"
            for key, row in boot.items() if key.startswith(f"{profile}-")
        )
        limits.append(lo)
        upper.append(hi)
        for i in range(1, 4):
            row = boot[f"{profile}-common{i}-128"]
            assert row["mib"] == 128 and row["status"] == "pass"
            common.append(row)
        for i in (1, 2):
            row = boot[f"{profile}-confirm{i}-{lo}"]
            assert row["mib"] == lo and row["status"] == "pass"
            confirmed.append(row)
    assert mode == "off" or all(r["sweep_loads"] > 0 for r in common + confirmed)
    return {
        "confirmed_capacity_mib": limits,
        "next_failed_mib": upper,
        "common_128mib": performance(common),
        "at_each_boot_capacity": performance(confirmed),
    }


def summarize(directory, demand=False):
    modes = ("off", "lz4", "demand") if demand else MODES
    result, totals = {}, []
    for mode in modes:
        boots = [json.loads((directory / f"{mode}{i}.json").read_text()) for i in (1, 2)]
        for boot in boots:
            validate_boot(boot, mode)
            if demand and mode != "off":
                backing = boot["backing"]
                cap = boot["config"]["arena_bytes"]
                idle = backing["idle_bytes"]
                assert idle == (boot["config"]["arena_overhead_bytes"] if mode == "demand" else cap)
                assert idle <= backing["peak_bytes"] <= cap
                for r in boot.values():
                    if r.get("status") == "pass":
                        assert idle <= r["arena_after_fill_bytes"] <= backing["peak_bytes"]
                        if mode == "demand":
                            assert r["arena_after_fill_bytes"] > idle
            totals.append(boot["config"]["memtotal_kib"])
        result[mode] = {p: compare_profile(boots, p, mode) for p in PROFILES}
    assert max(totals) - min(totals) < 1024
    for mode in modes:
        for profile in PROFILES:
            row = result[mode][profile]
            off = result["off"][profile]
            row["paired_capacity_multiple"] = [
                round(x / y, 3) for x, y in zip(row["confirmed_capacity_mib"], off["confirmed_capacity_mib"])
            ]
            row["common_worker_cpu_multiple"] = round(
                row["common_128mib"]["worker_cpu_s_per_gib"] / off["common_128mib"]["worker_cpu_s_per_gib"], 3
            )
    if demand:
        for profile in PROFILES:
            dynamic = result["demand"][profile]
            fixed = result["lz4"][profile]
            dynamic["paired_capacity_vs_fixed"] = [
                round(x / y, 3) for x, y in zip(dynamic["confirmed_capacity_mib"], fixed["confirmed_capacity_mib"])
            ]
            dynamic["common_worker_cpu_vs_fixed"] = round(
                dynamic["common_128mib"]["worker_cpu_s_per_gib"] / fixed["common_128mib"]["worker_cpu_s_per_gib"], 3
            )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--demand", action="store_true", help="compare demand-sized and fixed LZ4 against off")
    args = p.parse_args()
    directory = args.directory
    if not __debug__:
        p.error("Run with assertions enabled")
    (directory / "comparison.json").write_text(json.dumps(summarize(directory, demand=args.demand), indent=2) + "\n")
