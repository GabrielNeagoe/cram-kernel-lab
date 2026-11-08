"""Run four read-only SQLite search workers over private anonymous database images."""
import concurrent.futures
import ctypes as C
import json
import math
import mmap
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
corpus = ROOT / "corpus"
config = json.loads((corpus / "manifest.json").read_text())
mode, groups = sys.argv[1], int(sys.argv[2])
parts = config["parts"][:4*groups]
assert mode in {"off", "spill", "shuffle"} and len(parts) == 4*groups and groups > 0
MIB = 1024**2
budget = 320*MIB
debug = Path("/sys/kernel/debug/zswap")
def arena(name):
    return int((debug / ("arena_" + name)).read_text()) if mode != "off" else 0

def swap_counts():
    data = dict(line.split() for line in Path("/proc/vmstat").read_text().splitlines())
    return [int(data[k]) for k in ("pswpin", "pswpout")]

sql = C.CDLL("libsqlite3.so.0")
def bind(name, args, result=C.c_int):
    fn = getattr(sql, "sqlite3_" + name)
    fn.argtypes, fn.restype = args, result
    return fn
ptr, text, i64 = C.c_void_p, C.c_char_p, C.c_int64
open_db = bind("open", [text, C.POINTER(ptr)])
deserialize = bind("deserialize", [ptr,text,ptr,i64,i64,C.c_uint])
prepare = bind("prepare_v2",[ptr,text,C.c_int,C.POINTER(ptr),ptr])
step = bind("step",[ptr])
value = bind("column_int64",[ptr,C.c_int],i64)
finalize = bind("finalize",[ptr])
close = bind("close",[ptr])
sample = C.CDLL(str(ROOT / "faults.so")).sample_faults
sample.argtypes = [ptr,C.c_size_t,C.c_uint,C.POINTER(C.c_uint64),C.POINTER(C.c_int)]
sample.restype = C.c_int

class Partition:
    def __init__(self, spec):
        self.spec = spec
        size = spec["db_bytes"]
        self.image = mmap.mmap(-1,size,flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        self.image.madvise(mmap.MADV_NOHUGEPAGE)
        self.addr = C.addressof(C.c_char.from_buffer(self.image))
        fd = os.open(corpus / spec["file"], os.O_RDONLY | os.O_DIRECT)
        try:
            assert os.preadv(fd,[self.image],0) == size
        finally:
            os.close(fd)
        self.db = ptr()
        assert open_db(b":memory:", C.byref(self.db)) == 0
        # SQLITE_DESERIALIZE_READONLY. Caller retains the backing until close.
        assert deserialize(self.db,b"main",self.addr,size,size,4) == 0
        stmt = ptr()
        assert prepare(self.db,b"PRAGMA cache_size=-64",-1,C.byref(stmt),None) == 0
        assert step(stmt) == 101 and finalize(stmt) == 0
        self.evict()

    def evict(self):
        self.image.madvise(getattr(mmap,"MADV_PAGEOUT",21))

    def search(self, needle_id):
        needle = config["needles"][needle_id].encode().hex()
        query = f"SELECT count(*),sum(length(body)),sum(instr(body,x'{needle}')>0) FROM source"
        stmt = ptr()
        assert prepare(self.db,query.encode(),-1,C.byref(stmt),None) == 0
        try:
            assert step(stmt) == 100
            assert [value(stmt,i) for i in range(3)] == [
                self.spec["rows"], self.spec["source_bytes"], self.spec["hits"][needle_id]]
            assert step(stmt) == 101
        finally:
            assert finalize(stmt) == 0

cg = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split("::")[-1].lstrip("/")
assert cg.name == "workers"
trial_cg = cg.parent
peak = 0

def charge():
    # CRAM charges rounded payload slots through obj_cgroup_charge. Replace that
    # charge with physical backing and retain swap cache and other cgroup costs.
    current = int((trial_cg/"memory.current").read_text())
    reserved = int((trial_cg/"memory.cram.reserved").read_text())
    backing = arena("resident_bytes")
    return current - reserved + backing, current, reserved, backing

def check_budget():
    global peak
    cost, current, reserved, backing = charge()
    peak = max(peak,cost)
    if cost > budget:
        stat = dict(line.split() for line in (cg/"memory.stat").read_text().splitlines())
        print(json.dumps(dict(status="budget",parts=len(parts),db_bytes=total_bytes,
            peak_bytes=peak,
            cgroup_bytes=current,reserved_bytes=reserved,arena_bytes=backing,
            swapcached_bytes=int(stat["swapcached"]))),flush=True)
        os._exit(42)

def init_worker():
    with init_lock:
        cpu = next(cpus)
    os.sched_setaffinity(0,{cpu})
    barrier.wait()

init_lock = threading.Lock()
cpus = iter(sorted(os.sched_getaffinity(0)))
barrier = threading.Barrier(4)
assert os.sched_getaffinity(0) == set(range(4))
total_bytes = sum(p["db_bytes"] for p in parts)
swap = swap_counts()
idle = arena("entries")
assert idle == 0
loaded = []
cpu_ns = wall_ns = 0
samples = []
hits = 0
def query_part(part, needle_id):
    part.search(needle_id)
    part.evict()

def measure_part(part, offset):
    buf = (C.c_uint64 * (part.spec["db_bytes"]//4096//64+1))()
    resident = C.c_int()
    n = sample(part.addr,part.spec["db_bytes"],offset,buf,C.byref(resident))
    assert n >= 0
    return list(buf[:n]), resident.value

def run_batch(futures):
    while futures:
        done,futures = concurrent.futures.wait(futures,timeout=.01,
            return_when=concurrent.futures.FIRST_COMPLETED)
        for future in done:
            future.result()
        check_budget()

with concurrent.futures.ThreadPoolExecutor(4, initializer=init_worker) as pool:
    for i in range(0,len(parts),4):
        # Stage four disjoint database images, then check their resident footprint.
        futures = [pool.submit(Partition,spec) for spec in parts[i:i+4]]
        run_batch(futures)
        loaded.extend(f.result() for f in futures)
    backing_at_fill = arena("resident_bytes")
    entries_at_fill = arena("entries")
    fill_cost,fill_current,fill_reserved,fill_backing = charge()
    swapcache_at_fill=int(dict(line.split() for line in (cg/"memory.stat").read_text().splitlines())["swapcached"])
    loads0 = arena("loads")
    c,w = time.process_time_ns(),time.monotonic_ns()
    for sweep in range(3):
        for i in range(0,len(loaded),4):
            run_batch([pool.submit(query_part,part,(sweep+j+i)%len(config["needles"]))
                       for j,part in enumerate(loaded[i:i+4])])
    cpu_ns,wall_ns = time.process_time_ns()-c,time.monotonic_ns()-w
    restores = arena("loads")-loads0
    # Separate pass so the latency probe cannot warm the timed SQL workload.
    for i in range(0,len(loaded),4):
        futures = [pool.submit(measure_part,part,(i+j)%64) for j,part in enumerate(loaded[i:i+4])]
        run_batch(futures)
        for future in futures:
            times,already_resident = future.result()
            samples.extend(times)
            hits += already_resident
        for part in loaded[i:i+4]:
            part.evict()
    for part in loaded:
        assert close(part.db) == 0
        part.image.close()
assert arena("entries") == 0 and swap_counts() == swap
samples.sort()
print(json.dumps(dict(status="pass",parts=len(parts),db_bytes=total_bytes,source_bytes=sum(
    p["source_bytes"] for p in parts),files=sum(p["rows"] for p in parts),workers=4,
    verified_queries=3*len(parts),cpu_ns=cpu_ns,wall_ns=wall_ns,peak_bytes=peak,
    backing_at_fill=backing_at_fill,entries_at_fill=entries_at_fill,sql_restores=restores,
    charge_at_fill=fill_cost,
    cgroup_at_fill=fill_current,reservation_at_fill=fill_reserved,physical_backing_at_fill=fill_backing,
    swapcache_at_fill=swapcache_at_fill,disk_swap_io=0,fault_samples=len(samples),resident_samples=hits,
    fault_p99_ns=samples[math.ceil(.99*len(samples))-1] if samples else None)))
