"""Check matched occupancy, summarize kernel phase timings and recheck capacity."""
import argparse
import json
from pathlib import Path
from statistics import median

from summarize_capacity_trial import performance, summarize

MODES = ("off", "lz4", "demand", "indexed")
PHASES = ("search", "index", "codec", "hash")


def summarize_trial(directory):
    result = {"capacity": summarize(directory, demand=True, modes=MODES), "matched": {}}
    for profile, size, modes in [
        ("structured", 576, MODES[1:]),
        ("structured", 704, MODES[2:]),
        ("mixed", 192, MODES[1:]),
    ]:
        cases = {}
        result["matched"][f"{profile}-{size}"] = cases
        for mode in modes:
            boots = [json.loads((directory / f"{mode}{i}.json").read_text()) for i in (1, 2)]
            plain = [b[f"{profile}-matched-{size}"] for b in boots]
            timed = [b[f"{profile}-timed-{size}"] for b in boots]
            assert all(r["status"] == "pass" and r["mib"] == size for r in plain + timed)
            for row in timed:
                assert all(row[f"arena_{p}_ns"] > 0 for p in ("search", "codec", "hash"))
                assert (row["arena_index_ns"] > 0) == (mode == "indexed")
                assert row["sweep_loads"] == row["sweep_stores"] > 0
            cases[mode] = {
                "uninstrumented": performance(plain),
                "instrumented": performance(timed),
                "occupied_bytes_at_fill": [r["used_after_fill_bytes"] for r in timed],
                "entries_at_fill": [r["entries_after_fill"] for r in timed],
                "sweep_stores": [r["sweep_stores"] for r in timed],
                "phase_seconds_per_verified_gib": {
                    p: round(median(r[f"arena_{p}_ns"] / 1e9 * 1024 / r["verified_mib"] for r in timed), 5)
                    for p in PHASES
                },
                "search_ns_per_store": round(median(r["arena_search_ns"] / r["sweep_stores"] for r in timed), 1),
            }
        for field in ("occupied_bytes_at_fill", "entries_at_fill", "sweep_stores"):
            values = [v for case in cases.values() for v in case[field]]
            assert max(values) == min(values), (profile, size, field, values)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    if not __debug__:
        parser.error("Run with assertions enabled")
    (directory / "comparison.json").write_text(json.dumps(summarize_trial(directory), indent=2) + "\n")
