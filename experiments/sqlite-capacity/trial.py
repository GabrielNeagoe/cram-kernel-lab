"""Search a four-partition grid and repeat the common and capacity points."""
import hashlib
import json
import os
import time
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
mode = sys.argv[1]
params = Path("/sys/module/zswap/parameters")
debug = Path("/sys/kernel/debug/zswap")
def read(path):
    return path.read_text().strip()
assert mode in ("off","spill","shuffle")
assert read(params/"enabled") == ("N" if mode=="off" else "Y")
assert int(read(params/"cram_arena_mb")) == (0 if mode=="off" else 256)
for param in ("cram_spill","cram_skew_test","cram_two_shards","cram_demand","cram_index","cram_wide_hash","cram_shuffle"):
    assert read(params/param) == ("Y" if mode!="off" and (param!="cram_shuffle" or mode=="shuffle") else "N")
assert read(Path("/sys/devices/system/cpu/online")) == "0-3"
cg = Path("/sys/fs/cgroup") / read(Path("/proc/self/cgroup")).split("::")[-1].lstrip("/")
(cg/"memory.zswap.writeback").write_text("0")
assert read(cg/"memory.zswap.writeback") == "0"
# Isolate reclaim to the workload so it cannot evict the controller's pages.
supervisor, workers = cg/"supervisor", cg/"workers"
supervisor.mkdir()
for pid in read(cg/"cgroup.procs").split():
    (supervisor/"cgroup.procs").write_text(pid)
(cg/"cgroup.subtree_control").write_text("+memory")
workers.mkdir()
(workers/"memory.zswap.writeback").write_text("0")
def enter_workers():
    (workers/"cgroup.procs").write_text(str(os.getpid()))

def counters(path):
    return dict(line.split()[:2] for line in read(path).splitlines())
manifest_path = root/"corpus/manifest.json"
manifest = json.loads(manifest_path.read_text())
result = dict(mode=mode,ubuntu=next(x for x in read(Path("/etc/os-release")).splitlines()
            if x.startswith("PRETTY_NAME=")),sqlite=manifest["sqlite_version"],linux_source_commit=manifest["linux_commit"],
            manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),workers=4,
            budget_mib=320,arena_limit_mib=int(read(params/"cram_arena_mb")),points={})
idle = int(read(debug/"arena_resident_bytes")) if mode!="off" else 0
def point(label,groups):
    assert int(counters(Path("/proc/meminfo"))["MemAvailable:"]) > 4*1024**2
    disk = counters(Path("/proc/vmstat"))
    p = subprocess.run([sys.executable,root/"workload.py",mode,str(groups)],capture_output=True,text=True,timeout=240,preexec_fn=enter_workers)
    if p.returncode not in (0,42):
        raise RuntimeError(f"{label} {groups}: {p.returncode} {p.stdout} {p.stderr}")
    r = json.loads(p.stdout)
    assert r["status"] == ("pass" if p.returncode==0 else "budget")
    for k in ("pswpout","pswpin"):
        assert counters(Path("/proc/vmstat"))[k]==disk[k]
    assert counters(cg/"memory.events")["oom_kill"]=="0"
    if mode!="off":
        assert int(read(debug/"arena_resident_peak_bytes")) <= 256*1024**2
        for _ in range(40):
            if int(read(debug/"arena_entries")) == 0:
                break
            time.sleep(.05)
        assert int(read(debug/"arena_entries")) == int(read(debug/"arena_spill_live")) == 0, (
            read(debug/"arena_entries"), read(workers/"memory.cram.reserved"),read(cg/"memory.cram.reserved"))
        assert int(read(debug/"arena_resident_bytes")) == idle
        if r["status"]=="pass":
            assert r["sql_restores"]>0 and r["fault_samples"]>0
    result["points"][f"{label}-{groups}"] = r
    print(label,groups,r["status"],flush=True,file=sys.stderr)
    return not p.returncode

for n in range(3):
    assert point(f"matched{n}",12)
lo,hi = 12,len(manifest["parts"])//4
assert not point("search",hi), "Corpus exhausted without observing a capacity boundary"
while hi-lo>1:
    mid=(hi+lo)//2
    if point("search",mid):
        lo=mid
    else:
        hi=mid
while not (point("confirm1",lo) and point("confirm2",lo)):
    hi=lo
    lo-=1
    assert lo>=12
result.update(confirmed_groups=lo, next_failed_groups=hi, idle_bytes=idle)
print(json.dumps(result,indent=2))
