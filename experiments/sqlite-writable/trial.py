"""Search a four-partition grid and repeat the common and capacity points."""
import hashlib
import json
import os
import time
from pathlib import Path
import tempfile
import subprocess
import sys

root = Path(__file__).resolve().parent
mode = sys.argv[1]
params = Path("/sys/module/zswap/parameters")
debug = Path("/sys/kernel/debug/zswap")
def read(path):
    return path.read_text().strip()
assert mode in ("off","spill")
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
(workers/"memory.high").write_text(str(280*1024**2))
assert int(read(workers/"memory.high")) == 280*1024**2
(workers/"memory.zswap.writeback").write_text("0")
def enter_workers():
    (workers/"cgroup.procs").write_text(str(os.getpid()))

def counters(path):
    return dict(line.split()[:2] for line in read(path).splitlines())
manifest_path = root/"corpus/manifest.json"
manifest = json.loads(manifest_path.read_text())
result = dict(mode=mode,memory_high_mib=280,reference_sha256=hashlib.sha256((root/"reference.json").read_bytes()).hexdigest(),ubuntu=next(x for x in read(Path("/etc/os-release")).splitlines()
            if x.startswith("PRETTY_NAME=")),sqlite=manifest["sqlite_version"],linux_source_commit=manifest["linux_commit"],
            manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),workers=4,
            budget_mib=320,arena_limit_mib=int(read(params/"cram_arena_mb")),points={})
idle = int(read(debug/"arena_resident_bytes")) if mode!="off" else 0
def point(label,groups):
    assert int(counters(Path("/proc/meminfo"))["MemAvailable:"]) > 4*1024**2
    before_high=int(counters(workers/"memory.events")["high"])
    before_scan=int(counters(workers/"memory.stat")["pgscan"])
    disk = counters(Path("/proc/vmstat"))
    start=time.monotonic()
    with tempfile.NamedTemporaryFile() as marker:
        env = dict(os.environ, SQLITE_TRIAL_PROGRESS=marker.name)
        with subprocess.Popen([sys.executable,root/"workload.py",mode,str(groups)],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                              env=env,preexec_fn=enter_workers) as proc:
            last_progress, completed = start, 0
            while True:
                try:
                    output, errors = proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    now=time.monotonic()
                    batches=os.fstat(marker.fileno()).st_size
                    if batches != completed:
                        last_progress,completed = now,batches
                    if now-last_progress<45 and now-start<240:
                        continue
                    current=int(read(cg/"memory.current"))
                    reserved=int(read(cg/"memory.cram.reserved"))
                    backing=int(read(debug/"arena_resident_bytes")) if mode!="off" else 0
                    r=dict(status="stalled" if now-last_progress>=45 else "timeout",
                           parts=4*groups,db_bytes=sum(x["db_bytes"] for x in manifest["parts"][:4*groups]),
                           completed_batches=completed,seconds_without_progress=round(now-last_progress,2),
                           cgroup_bytes=current,reserved_bytes=reserved,arena_bytes=backing,
                           sampled_charge_bytes=current-reserved+backing)
                    proc.kill()
                    proc.communicate(timeout=30)
                    break
                if proc.returncode not in (0,42):
                    raise RuntimeError(f"{label} {groups}: {proc.returncode} {output} {errors}")
                r=json.loads(output)
                assert r["status"] == ("pass" if proc.returncode==0 else "budget")
                break
    r["elapsed_seconds"]=round(time.monotonic()-start,3)
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
    r["high_events"]=int(counters(workers/"memory.events")["high"])-before_high
    r["pages_scanned"]=int(counters(workers/"memory.stat")["pgscan"])-before_scan
    result["points"][f"{label}-{groups}"] = r
    print(label,groups,r["status"],flush=True,file=sys.stderr)
    return r["status"]=="pass"

for n in range(3):
    assert point(f"matched{n}",15)
for n in range(3):
    point(f"near{n}",16)
lo,hi = 15,17
while point("search",hi):
    lo = hi
    hi = min(2*hi,len(manifest["parts"])//4)
    assert hi > lo, "Corpus exhausted without a capacity boundary"
while hi-lo>1:
    mid=(hi+lo)//2
    if point("search",mid):
        lo=mid
    else:
        hi=mid
while not (point("confirm1",lo) and point("confirm2",lo)):
    hi=lo
    lo-=1
    assert lo>=15
result.update(confirmed_groups=lo, next_failed_groups=hi, idle_bytes=idle)
print(json.dumps(result,indent=2))
