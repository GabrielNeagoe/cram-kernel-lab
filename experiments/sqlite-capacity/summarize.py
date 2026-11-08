"""Check the six application boot records and recompute capacity and medians."""
import argparse
import json
import hashlib
from pathlib import Path
from statistics import median


def stats(rows):
    def median_value(fn):
        return round(median(fn(r) for r in rows), 4)
    latency = [r["fault_p99_ns"]/1000 for r in rows if r["fault_samples"]]
    return dict(runs=len(rows), fault_samples=sum(r["fault_samples"] for r in rows),
                resident_samples=sum(r["resident_samples"] for r in rows),
                median_run_p99_us=round(median(latency),3) if latency else None,
                cpu_s_per_gib=median_value(lambda r: r["cpu_ns"]/1e9*2**30/(3*r["db_bytes"])),
                db_mib_per_s=median_value(lambda r: 3*r["db_bytes"]/2**20/(r["wall_ns"]/1e9)),
                scans_restored_pages=median_value(lambda r:r["sql_restores"]),
                database_mib=median_value(lambda r:r["db_bytes"]/2**20),
                peak_charged_mib=median_value(lambda r:r["peak_bytes"]/2**20),
                swapcache_at_fill_mib=median_value(lambda r:r["swapcache_at_fill"]/2**20),
                backing_at_fill_mib=median_value(lambda r:r["backing_at_fill"]/2**20))


def compare(directory):
    out = {}
    manifests,common_sizes,host_counts = set(),set(),set()
    for mode in ("off","spill","shuffle"):
        sizes, next_sizes, matched, confirmed = [], [], [], []
        for n in (1,2):
            boot = json.loads((directory/f"{mode}{n}.json").read_text())
            assert boot["mode"] == mode and boot["host_vmx_samples"] > 0
            assert boot["host"]["vmx_min"] == boot["host"]["vmx_max"]
            host_counts.add(boot["host"]["vmx_min"])
            assert boot["arena_limit_mib"] == (0 if mode=="off" else 256)
            assert boot["budget_mib"] == 320 and boot["workers"] == 4
            manifests.add(boot["manifest_sha256"])
            lo,hi = boot["confirmed_groups"],boot["next_failed_groups"]
            assert hi == lo + 1
            points = boot["points"]
            for key,r in points.items():
                assert r["parts"] == 4*int(key.split("-")[-1])
                if r["status"]=="budget":
                    assert r["peak_bytes"] > 320*2**20
                    assert r["peak_bytes"] == r["cgroup_bytes"] - r["reserved_bytes"] + r["arena_bytes"]
                    continue
                assert r["status"]=="pass" and r["workers"]==4 and r["verified_queries"]==3*r["parts"]
                assert r["peak_bytes"] <= 320*2**20 and r["disk_swap_io"] == 0
                assert r["charge_at_fill"] == r["cgroup_at_fill"] - r["reservation_at_fill"] + r["physical_backing_at_fill"]
                if mode!="off":
                    assert r["sql_restores"]>0 and r["fault_samples"]>0 and r["backing_at_fill"]<=256*2**20
            sizes.append(points[f"confirm1-{lo}"]["db_bytes"])
            assert sizes[-1] == points[f"confirm2-{lo}"]["db_bytes"]
            next_sizes.append(next(r["db_bytes"] for r in points.values()
                                   if r["parts"]==4*hi and r["status"]=="budget"))
            for i in range(3):
                r=points[f"matched{i}-12"]
                common_sizes.add(r["db_bytes"])
                matched.append(r)
            confirmed.extend(points[f"confirm{i}-{lo}"] for i in (1,2))
        baseline=sizes if mode=="off" else out["off"]["confirmed_bytes"]
        out[mode]=dict(confirmed_bytes=sizes,next_failed_bytes=next_sizes,
                       paired_capacity_multiple=[round(x/y,4) for x,y in zip(sizes,baseline)],
                       matched=stats(matched),at_capacity=stats(confirmed))
    assert len(manifests)==len(common_sizes)==len(host_counts)==1
    assert manifests == {hashlib.sha256((directory/"corpus.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest()}
    return out


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    directory=parser.parse_args().directory
    if not __debug__:
        parser.error("Enable Python assertions")
    (directory/"comparison.json").write_text(json.dumps(compare(directory),indent=2)+"\n")
