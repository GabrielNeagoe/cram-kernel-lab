"""Validate untraced matched boots and summarize pressure-point costs."""
import argparse
import json
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sqlite-writable"))
from summarize_writes import compare, summarize_runs


def validate_diagnostics(folder):
    for name in ("trace1", "trace2", "trace3", "trace4"):
        boot = json.loads((folder / f"{name}.json").read_text())
        assert boot["workers"] == 4 and boot.get("policy", boot["mode"]) == "spill"
        assert boot["arena_limit_mib"] == 256 and boot["memory_high_mib"] == 280
        assert boot["host"]["vmx_min"] == boot["host"]["vmx_max"] == 1
        row = boot["points"]["diagnostic-31"]
        assert row["parts"] == 124 and row["trace"]
        assert row["status"] in ("stalled", "timeout", "budget", "pass")
        for window in row["trace"]:
            if name == "trace4":
                assert window["seconds"] >= 1 and window["probe_misses"] == 0
                assert all(r["anon"] in (0,1) for r in window["anonymous_mapping"])
                continue
            if name == "trace3":
                assert window["seconds"] >= 1
                assert window["folios"]["visits"] >= window["folios"]["distinct"]
                assert "refs" in window and "swap_alloc" in window
                continue
            assert window["seconds"] >= 3 and window["worker_threads"] == 5
            assert window["writeout_calls"] >= window["distinct_folios"] >= window["repeated_folios"]
            assert {r["lru"] for r in window["isolate"]} <= {0, 2}
            assert window["arena_delta"]["spill_stores"] <= window["arena_delta"]["spill_tries"]


def summarize(folder):
    validate_diagnostics(folder)
    results = compare(folder, ("off","spill","net","ra"), folder.parent/"sqlite-write-01")
    for policy, result in results.items():
        pressures, targets, first_failures = [], [], []
        for n in (1,2):
            boot=json.loads((folder/f"{policy}{n}.json").read_text())
            assert boot["policy"]==policy and boot["mglru"]=="0x0007"
            if policy=="ra":
                assert [boot["page_cluster_"+s] for s in ("before","tested","restored")] == [3,0,3]
            points=boot["points"]
            assert all("trace" not in r for r in points.values())
            if policy!="net":
                assert all(r["net_backoffs"]==0 for r in points.values())
            first_failures.append(next(r for r in points.values()
                if r["parts"]==4*boot["next_failed_groups"] and r["status"]!="pass"))
            if policy=="off":
                continue
            pressures.extend(points[f"pressure{i}-30"] for i in range(3))
            for group in (31,32):
                row=next(r for r in points.values() if r["parts"]==4*group)
                targets.append(dict(boot=n,groups=group,**row))
        result["targets"]=targets
        result["first_failures"]=first_failures
        if pressures:
            good=[r for r in pressures if r["status"]=="pass"]
            result["pressure_passes"]=len(good)
            result["pressure"]=summarize_runs(good) if good else None
            result["pressure_backoffs"]=median(r["net_backoffs"] for r in pressures)
    return results


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",type=Path)
    folder=parser.parse_args().folder
    if not __debug__:
        parser.error("Enable assertions")
    (folder/"comparison.json").write_text(json.dumps(summarize(folder),indent=2)+"\n")
