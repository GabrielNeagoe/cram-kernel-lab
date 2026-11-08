"""Summarize matched hash-table and spinlock profiles on the two-vCPU VMware guest."""
import argparse
import json
from pathlib import Path
from statistics import median

from summarize_capacity_trial import compare_profile, performance, validate_boot


def lock_profile(rows):
    def ratio(numerator, denominator, scale=1):
        return round(median(r[numerator] * scale / r[denominator] for r in rows), 5)

    def per_gib(counter):
        return ratio(counter, "verified_mib", 1024 / 1e9)

    assert all(0 <= r["arena_contended"] <= r["arena_acquires"] for r in rows)
    assert all(r["arena_lookups"] > 0 and r["arena_hold_ns"] >= r["arena_hash_ns"] > 0 for r in rows)
    waits = [r["arena_wait_ns"] / r["arena_contended"] / 1000 for r in rows if r["arena_contended"]]
    return {
        "nodes_per_lookup": ratio("arena_visits", "arena_lookups"),
        "hash_ns_per_lookup": ratio("arena_hash_ns", "arena_lookups"),
        "contended_percent": ratio("arena_contended", "arena_acquires", 100),
        "hold_us_per_acquire": ratio("arena_hold_ns", "arena_acquires", 0.001),
        "wait_us_per_contended_acquire": round(median(waits), 3) if waits else None,
        "wait_s_per_gib": per_gib("arena_wait_ns"),
        "hold_s_per_gib": per_gib("arena_hold_ns"),
        "hash_s_per_gib": per_gib("arena_hash_ns"),
        "codec_s_per_gib": per_gib("arena_codec_ns"),
        "acquires": [r["arena_acquires"] for r in rows],
        "contended": [r["arena_contended"] for r in rows],
    }


def summarize(directory, shards=False):
    capacity, matched, totals = {}, {}, []
    modes = ("off", "wide", "sharded") if shards else ("off", "indexed", "wide")
    phases = ("plain", "repeatA", "repeatB") if shards else ("plain",)
    for mode in modes:
        boots = [json.loads((directory / f"{mode}{i}.json").read_text()) for i in (1, 2)]
        for boot in boots:
            validate_boot(boot, mode)
            totals.append(boot["config"]["memtotal_kib"])
            for key, r in boot.items():
                if r.get("status") == "pass" and not any(f"-{tag}" in key for tag in (*phases, "probe")):
                    assert r["workers"] == 2
            if mode != "off":
                backing = boot["backing"]
                assert backing["idle_bytes"] == boot["config"]["arena_overhead_bytes"]
                assert backing["peak_bytes"] <= boot["config"]["arena_bytes"]
        capacity[mode] = {}
        for profile, size in (("structured", 704), ("mixed", 480)):
            cap = compare_profile(boots, profile, mode)
            capacity[mode][profile] = cap
            cap["paired_multiple_vs_off"] = [round(x / y, 3) for x, y in zip(
                cap["confirmed_capacity_mib"], capacity["off"][profile]["confirmed_capacity_mib"])]
            if mode == "off":
                continue
            for workers in (1, 2):
                by_boot = [[b[f"{profile}-{tag}{workers}-{size}"] for tag in phases] for b in boots]
                plain = [row for group in by_boot for row in group]
                probe = [b[f"{profile}-probe{workers}-{size}"] for b in boots]
                assert all(r["status"] == "pass" and r["workers"] == workers and r["mib"] == size for r in plain + probe)
                assert all(r["sweep_stores"] == r["sweep_loads"] > 0 for r in plain + probe)
                matched.setdefault(profile, {})[f"{mode}-{workers}"] = {
                    "mib": size, "workers": workers, "uninstrumented": performance(plain),
                    "instrumented": performance(probe), "lock_profile": lock_profile(probe),
                    "live_entries": [r["entries_after_fill"] for r in probe],
                    "occupied_bytes": [r["used_after_fill_bytes"] for r in probe],
                    "sweep_stores": [r["sweep_stores"] for r in plain + probe],
                }
                case = matched[profile][f"{mode}-{workers}"]
                if shards:
                    case["uninstrumented_by_boot"] = [performance(group) for group in by_boot]
                if mode == "sharded":
                    shares = []
                    for r in probe:
                        a, b = r["arena_shard0_stores"], r["arena_shard1_stores"]
                        assert a > 0 and b > 0 and a + b == r["sweep_stores"]
                        shares.append(round(100*a/(a+b), 3))
                    case["shard0_store_percent_by_boot"] = shares
    assert max(totals) - min(totals) < 1024
    for cases in matched.values():
        for key in ("live_entries", "occupied_bytes", "sweep_stores"):
            assert len({value for case in cases.values() for value in case[key]}) == 1
    return {"matched": matched, "capacity": capacity}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--shards", action="store_true", help="compare two wide-hash shards with one")
    args = parser.parse_args()
    directory = args.directory
    if not __debug__:
        parser.error("Run with assertions enabled")
    (directory / "comparison.json").write_text(json.dumps(summarize(directory, shards=args.shards), indent=2) + "\n")
