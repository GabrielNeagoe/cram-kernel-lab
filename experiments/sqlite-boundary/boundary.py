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
high_params=Path("/sys/module/memcontrol/parameters")
high_net=read(high_params/"cram_high_net")=="Y"
assert high_net == (os.environ.get("CRAM_HIGH_NET")=="1")
result["policy"]="net" if high_net else mode
result["mglru"]=read(Path("/sys/kernel/mm/lru_gen/enabled"))
idle = int(read(debug/"arena_resident_bytes")) if mode!="off" else 0
diagnostic = os.environ.get("CRAM_DIAGNOSTIC")=="1"
assert read(Path("/proc/sys/vm/page-cluster"))=="0"
result["page_cluster"]=0

def snapshot(pid, batch, elapsed):
    vm=counters(workers/"memory.stat")
    arena=("resident_bytes","used_bytes","entries","stores","loads","spill_tries","spill_stores",
           "reject_quota","reject_poor","chunk_alloc_failures","last_request")
    values={name:int(read(debug/("arena_"+name))) for name in arena}
    pressure=read(workers/"memory.pressure").splitlines()
    stacks=[]
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            frames=[line.split("] ")[-1].split("+")[0] for line in read(task/"stack").splitlines()]
        except (FileNotFoundError,ProcessLookupError):
            continue
        if frames: stacks.append(frames)
    return dict(second=round(elapsed,2),batch=batch,arena=values,
                stat={k:int(vm[k]) for k in ("anon","swapcached","zswap","pgscan","pgsteal","pgmajfault")},
                pressure_us={line.split()[0]:int(line.split("total=")[1]) for line in pressure},
                high_events=int(counters(workers/"memory.events")["high"]),stacks=stacks)

def point(label,groups):
    assert int(counters(Path("/proc/meminfo"))["MemAvailable:"]) > 4*1024**2
    before_high=int(counters(workers/"memory.events")["high"])
    before_scan=int(counters(workers/"memory.stat")["pgscan"])
    disk = counters(Path("/proc/vmstat"))
    snapshots=[]
    backoffs=int(read(high_params/"cram_high_backoffs"))
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
                    if diagnostic and (not snapshots or now-start-snapshots[-1]["second"]>=5):
                        snapshots.append(snapshot(proc.pid,completed,now-start))
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
    if snapshots: r["trace"]=snapshots
    r["net_backoffs"]=int(read(high_params/"cram_high_backoffs"))-backoffs
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

if diagnostic:
    point("trace",32)
else:
    for n in range(2):
        assert point(f"matched{n}",31)
    for group in range(32,len(manifest["parts"])//4+1):
        if not point("boundary",group):
            result["first_failure"]=group
            break
        if not point("confirm",group):
            result["first_failure"]=group
            break
    result["confirmed_groups"]=result["first_failure"]-1
print(json.dumps(result,indent=2))
