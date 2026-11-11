"""Regenerate per-boot results from the WAL measurements."""
import json
import math
from pathlib import Path
import sys

MIB=2**20

def summarize(folder):
    parts=json.loads((folder.parent/"sqlite-01/corpus.json").read_text())["parts"]
    rows=[]
    for arm in ("off1","small1","small2","off2","smalllarge1","offlarge1","offlarge2","smalllarge2"):
        boot=json.loads((folder/f"{arm}.json").read_text())
        compressed=arm.startswith("small")
        assert boot["host"]["vmx_min"]==boot["host"]["vmx_max"]==1
        assert boot["online"]=="0-3" and boot["high_bytes"]==280*MIB
        assert boot["arena_limit_mib"]==(256 if compressed else 0)
        assert boot["page_cluster_tested"]==0 and boot["page_cluster_restored"]==3
        assert [p["files"] for p in boot["points"]]==[64,128]
        for p in boot["points"]:
            count=p["files"]
            expected=sum(e["db_bytes"] for e in parts[:count])
            cache=boot.get("cache_kib",1024)
            assert cache==(3072 if "large" in arm else 1024)
            row=dict(boot=arm,cache_kib=cache,files=count,database_mib=expected/MIB,status=p["status"],
                     peak_mib=p["peak_bytes"]/MIB,stores=p["stores"],loads=p["loads"],high_events=p["high_events"])
            if p["status"]=="pass":
                assert p["verified_files"]==count and p["db_bytes"]==expected and p["peak_bytes"]<=320*MIB
                for name,values in p["latency_ns"].items():
                    assert len(values)==3*count and min(values)>0
                    row[name+"_p99_ms"]=sorted(values)[math.ceil(.99*len(values))-1]/1e6
                row["cpu_s_per_gib"]=p["cpu_ns"]/1e9/(3*expected/2**30)
                row["mib_per_s"]=3*expected/MIB/(p["wall_ns"]/1e9)
                row["read_mib"]=p["io_bytes"]["read_bytes"]/MIB
                row["write_mib"]=p["io_bytes"]["write_bytes"]/MIB
            elif p["status"]=="budget":
                assert p["peak_bytes"]>320*MIB
            else:
                assert p["status"]=="timeout"
            rows.append(row)
    return rows

if __name__=="__main__":
    assert __debug__
    folder=Path(sys.argv[1])
    (folder/"comparison.json").write_text(json.dumps(summarize(folder),indent=2)+"\n")
