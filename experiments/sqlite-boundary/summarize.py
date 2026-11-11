"""Validate the 544.8 MiB follow-up and summarize paired untraced boots."""
import hashlib
import json
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"sqlite-writable"))
from summarize_writes import compare, summarize_runs

MIB=2**20

def load(path):
    return json.loads(path.read_bytes())

def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n",b"\n")).hexdigest()

def summarize(folder):
    previous=folder.parent/"sqlite-reclaim-01"
    off=compare(folder,("off",),folder.parent/"sqlite-write-01")["off"]
    base=off["confirmed_bytes"]
    assert base==load(previous/"comparison.json")["off"]["confirmed_bytes"]
    assert len(set(base))==1
    corpus=folder.parent/"sqlite-01/corpus.json"
    reference=folder.parent/"sqlite-write-01/reference.json"
    parts=load(corpus)["parts"]
    expected=load(reference)
    summaries={"off":off}
    for policy,slot in (("standard",256),("small",128)):
        matched,capacity,failures,ratios,targets=[],[],[],[],[]
        for n in (1,2):
            b=load(folder/f"{policy}{n}.json")
            assert b["mode"]=="spill" and b["policy"]==policy and b["workers"]==4
            assert b["host"]["vmx_max"]==b["host"]["vmx_min"]==1 and b["host_vmx_samples"]>0
            assert b["mglru"]=="0x0007" and b["arena_limit_mib"]==256
            assert b["memory_high_mib"]==280 and b["budget_mib"]==320
            assert [b[k] for k in ("page_cluster_before","page_cluster","page_cluster_restored")]==[3,0,3]
            assert len(b["geometry_log"])==2 and all(f"block={slot} " in l for l in b["geometry_log"])
            assert b["manifest_sha256"]==digest(corpus) and b["reference_sha256"]==digest(reference)
            points=b["points"]
            lo=b["confirmed_groups"]
            assert b["first_failure"]==lo+1
            for key,r in points.items():
                assert "trace" not in r and r["net_backoffs"]==0
                count=r["parts"]
                assert count==4*int(key.split("-")[-1])
                assert r["db_bytes"]==sum(p["db_bytes"] for p in parts[:count])
                if r["status"]=="pass":
                    assert r["verified_queries"]==r["commits"]==3*count
                    assert r["rollbacks"]==r["verified_images"]==count
                    assert r["rows_modified"]==3*sum(expected[p["file"]]["changed"] for p in parts[:count])
                    assert r["disk_swap_io"]==0 and r["peak_bytes"]<=320*MIB
                    assert r["backing_at_fill"]<=256*MIB and r["sql_restores"]>0
                elif r["status"]=="budget":
                    assert r["peak_bytes"]>320*MIB
                else:
                    assert r["status"] in ("stalled","timeout")
                    assert r["elapsed_seconds"] >= (45 if r["status"]=="stalled" else 240)
            passed=[r for r in points.values() if r["parts"]==4*lo and r["status"]=="pass"]
            assert len(passed)==2
            capacity.extend(passed)
            ratios.append(passed[0]["db_bytes"]/base[n-1])
            matched.extend(points[f"matched{i}-31"] for i in range(2))
            targets.extend(r for r in points.values() if r["parts"]==128)
            failures.append(next(r for r in points.values() if r["parts"]==4*(lo+1) and r["status"]!="pass"))
        summaries[policy]=dict(paired_ratio=ratios,at_527=summarize_runs(matched),
                               capacity=summarize_runs(capacity),first_failures=failures,
                               matched_bytes_per_entry=median(r["reservation_at_fill"]/r["entries_at_fill"] for r in matched),
                               matched_entries=median(r["entries_at_fill"] for r in matched),
                               target_outcomes=[r["status"] for r in targets],
                               at_544=summarize_runs(targets) if all(r["status"]=="pass" for r in targets) else None)
    b=load(folder/"trace1.json")
    assert b["host"]["vmx_min"]==b["host"]["vmx_max"]==1 and b["workers"]==4
    assert b["arena_limit_mib"]==256 and b["memory_high_mib"]==280 and b["budget_mib"]==320
    trace=b["points"]["trace-32"]
    assert trace["parts"]==128 and trace["status"]=="timeout" and trace["elapsed_seconds"]>=240
    windows=trace["trace"]
    saturated=[w for w in windows if w["arena"]["resident_bytes"]==256*MIB]
    assert saturated and b["page_cluster"]==0
    first,last=saturated[0],saturated[-1]
    delta={k:last["arena"][k]-first["arena"][k] for k in
           ("spill_stores","spill_tries","stores","loads","reject_quota","reject_poor","chunk_alloc_failures")}
    assert all(delta[k]==0 for k in ("reject_quota","reject_poor","chunk_alloc_failures"))
    summaries["trace"]=dict(saturated_samples=len(saturated),total_samples=len(windows),first=first["second"],
                             last=last["second"],first_batch=first["batch"],last_batch=last["batch"],deltas=delta,
                             scan_delta=last["stat"]["pgscan"]-first["stat"]["pgscan"],
                             charged_mib_at_exit=trace.get("sampled_charge_bytes",trace.get("peak_bytes"))/MIB)
    candidate=load(folder/"smalltrace.json")
    record=candidate["points"]["trace-32"]
    assert candidate["policy"]=="small" and candidate["page_cluster"]==0 and record["status"]=="pass"
    assert record["verified_images"]==record["parts"]==128
    assert record["disk_swap_io"]==0 and record["peak_bytes"]<=320*MIB
    assert candidate["host"]["vmx_min"]==candidate["host"]["vmx_max"]==1
    assert candidate["workers"]==4 and candidate["arena_limit_mib"]==256
    data=record["trace"]
    summaries["candidate_trace"]=dict(samples=len(data),elapsed=record["elapsed_seconds"],
        scans=record["pages_scanned"],highest_sampled_arena_mib=max(s["arena"]["resident_bytes"] for s in data)/MIB,
        at_fill_mib=record["backing_at_fill"]/MIB)
    return summaries

if __name__=="__main__":
    assert __debug__
    directory=Path(sys.argv[1])
    (directory/"comparison.json").write_text(json.dumps(summarize(directory),indent=2)+"\n")
