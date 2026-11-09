"""Validate writable SQLite transactions and summarize fresh-boot pairs."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sqlite-capacity"))
from summarize import stats as scan_stats


def summarize_runs(rows):
    summary = scan_stats(rows)
    for key in ("high_events", "pages_scanned", "rows_modified", "commits", "rollbacks"):
        summary[key] = median(r[key] for r in rows)
    summary["runs_with_sampled_faults"] = sum(r["fault_samples"] > 0 for r in rows)
    return summary


def compare(folder, policies=("off", "spill"), reference_folder=None):
    ref_bytes = ((reference_folder or folder) / "reference.json").read_bytes().replace(b"\r\n", b"\n")
    reference = json.loads(ref_bytes)
    corpus = (folder.parent / "sqlite-01/corpus.json").read_bytes().replace(b"\r\n", b"\n")
    parts = json.loads(corpus)["parts"]
    results = {}
    for policy in policies:
        mode = "off" if policy == "off" else "spill"
        capacities, failures, matched, near, confirmed = [], [], [], [], []
        for n in (1, 2):
            b = json.loads((folder / f"{policy}{n}.json").read_text())
            assert b["mode"] == mode and b["workers"] == 4
            assert b["host_vmx_samples"] > 0 and b["host"]["vmx_min"] == b["host"]["vmx_max"] == 1
            assert b["reference_sha256"] == hashlib.sha256(ref_bytes).hexdigest()
            assert b["manifest_sha256"] == hashlib.sha256(corpus).hexdigest()
            assert b["memory_high_mib"] == 280 and b["budget_mib"] == 320
            assert b["arena_limit_mib"] == (256 if mode == "spill" else 0)
            lo, hi = b["confirmed_groups"], b["next_failed_groups"]
            assert hi == lo + 1
            points = b["points"]
            for key, row in points.items():
                count = row["parts"]
                assert count == 4 * int(key.split("-")[-1])
                assert row["db_bytes"] == sum(p["db_bytes"] for p in parts[:count])
                if row["status"] in ("stalled","timeout"):
                    assert row["elapsed_seconds"] >= (45 if row["status"]=="stalled" else 240)
                    continue
                if row["status"] == "budget":
                    assert row["peak_bytes"] == row["cgroup_bytes"] - row["reserved_bytes"] + row["arena_bytes"]
                    assert row["peak_bytes"] > 320 * 2**20
                    continue
                assert row["status"] == "pass" and row["peak_bytes"] <= 320 * 2**20
                assert row["verified_queries"] == row["commits"] == 3 * count
                assert row["verified_images"] == row["rollbacks"] == count
                assert row["rows_modified"] == 3 * sum(reference[p["file"]]["changed"] for p in parts[:count])
                assert row["disk_swap_io"] == 0 and row["workers"] == 4
                assert row["backing_at_fill"] <= 256 * 2**20
                assert row["charge_at_fill"] == row["cgroup_at_fill"] - row["reservation_at_fill"] + row["physical_backing_at_fill"]
            capacity = [points[f"confirm{i}-{lo}"] for i in (1, 2)]
            assert all(r["status"] == "pass" for r in capacity)
            assert all(r["high_events"] > 0 for r in capacity)
            if mode == "spill":
                assert all(r["sql_restores"] > 0 for r in capacity)
            capacities.append(capacity[0]["db_bytes"])
            failures.append(next(r["db_bytes"] for r in points.values()
                                 if r["parts"] == 4*hi and r["status"] != "pass"))
            near.extend(points[f"near{i}-16"] for i in range(3))
            confirmed.extend(capacity)
            matched.extend(points[f"matched{i}-15"] for i in range(3))
        baseline = capacities if mode == "off" else results["off"]["confirmed_bytes"]
        results[policy] = dict(confirmed_bytes=capacities, first_failed_bytes=failures,
                             paired_multiple=[x/y for x,y in zip(capacities,baseline)],
                             matched=summarize_runs(matched),
                             near=summarize_runs(near) if all(r["status"]=="pass" for r in near) else None,
                             at_capacity=summarize_runs(confirmed))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",type=Path)
    folder = parser.parse_args().folder
    if not __debug__:
        parser.error("Enable Python assertions")
    (folder/"comparison.json").write_text(json.dumps(compare(folder),indent=2)+"\n")
