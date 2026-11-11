"""Run fresh writable copies under cgroup pressure and check physical budget."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

root=Path.cwd()
cg=Path("/sys/fs/cgroup")/Path("/proc/self/cgroup").read_text().strip().split("::")[-1].lstrip("/")
supervisor,workers=cg/"supervisor",cg/"workers"
supervisor.mkdir()
(supervisor/"cgroup.procs").write_text(str(os.getpid()))
# The wrapper and systemd service shell are also direct members.
for pid in (cg/"cgroup.procs").read_text().split():
    (supervisor/"cgroup.procs").write_text(pid)
(cg/"cgroup.subtree_control").write_text("+memory")
workers.mkdir()
workers.joinpath("memory.high").write_text(str(280*2**20))
workers.joinpath("memory.zswap.writeback").write_text("0")
mode=sys.argv[1]
params=Path("/sys/module/zswap/parameters")
assert mode in ("off","spill")
assert (params/"enabled").read_text().strip()==("N" if mode=="off" else "Y")
assert int((params/"cram_arena_mb").read_text())==(0 if mode=="off" else 256)
assert int(Path("/proc/sys/vm/page-cluster").read_text())==0
assert (params/"cram_zstd1").read_text().strip()=="N"
manifest=json.loads((root/"corpus/manifest.json").read_text())
debug=Path("/sys/kernel/debug/zswap")
def num(path):
    return int(path.read_text())
def stats(path):
    return {k:int(v) for k,v in (line.split() for line in path.read_text().splitlines())}
def arena(name):
    return num(debug/("arena_"+name)) if mode=="spill" else 0
def enter():
    (workers/"cgroup.procs").write_text(str(os.getpid()))
result={"mode":mode,"points":[],"online":Path("/sys/devices/system/cpu/online").read_text().strip(),
        "arena_limit_mib":int((params/"cram_arena_mb").read_text()),"high_bytes":num(workers/"memory.high")}
assert result["online"]=="0-3"
for count in (64,128):
    with tempfile.TemporaryDirectory(dir=root) as directory:
        folder=Path(directory)
        for part in manifest["parts"][:count]:
            dest=folder/part["file"]
            shutil.copyfile(root/"corpus"/part["file"],dest)
            with dest.open("rb") as handle:
                os.fsync(handle.fileno())
                os.posix_fadvise(handle.fileno(),0,0,os.POSIX_FADV_DONTNEED)
        # Evict supervisor's source-file cache as well so copies do not inflate trial charge.
        for part in manifest["parts"][:count]:
            with (root/"corpus"/part["file"]).open("rb") as handle:
                os.posix_fadvise(handle.fileno(),0,0,os.POSIX_FADV_DONTNEED)
        disk=stats(Path("/proc/vmstat"))
        high=stats(workers/"memory.events")["high"]
        stored,loaded=arena("stores"),arena("loads")
        peak=0
        with subprocess.Popen([sys.executable,"workload.py",directory,str(count)],preexec_fn=enter,
                              text=True,stdout=subprocess.PIPE) as p:
            start=time.monotonic()
            while True:
                charge=num(cg/"memory.current")-num(cg/"memory.cram.reserved")+arena("resident_bytes")
                peak=max(peak,charge)
                if time.monotonic()-start>240 or charge>320*2**20:
                    p.kill()
                    p.communicate()
                    record={"status":"budget" if charge>320*2**20 else "timeout"}
                    break
                try:
                    output,_=p.communicate(timeout=.02)
                except subprocess.TimeoutExpired:
                    continue
                assert p.returncode==0
                record=dict(status="pass",**json.loads(output))
                break
        record.update(files=count,peak_bytes=peak,stores=arena("stores")-stored,loads=arena("loads")-loaded,
            high_events=stats(workers/"memory.events")["high"]-high)
        after=stats(Path("/proc/vmstat"))
        assert all(after[k]==disk[k] for k in ("pswpout","pswpin"))
        assert stats(workers/"memory.events")["oom_kill"]==0
        assert arena("resident_peak_bytes")<=256*2**20 and arena("entries")==0
        result["points"].append(record)
        print(count,record["status"],file=sys.stderr,flush=True)
print(json.dumps(result))
