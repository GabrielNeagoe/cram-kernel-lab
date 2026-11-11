"""Four pinned writers on separate WAL-mode SQLite files, using the published corpus."""
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

root = Path.cwd()
manifest = json.loads((root/"corpus/manifest.json").read_text())
reference = json.loads((root/"reference.json").read_text())
folder = Path(sys.argv[1])
parts = manifest["parts"][:int(sys.argv[2])]
barrier = threading.Barrier(4, timeout=60)


def writer(cpu):
    os.sched_setaffinity(0, {cpu})
    connections = []
    for part in parts[cpu::4]:
        db = sqlite3.connect(folder/part["file"], isolation_level=None)
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA mmap_size=0")
        cache_kib=int(os.environ.get("SQLITE_CACHE_KIB","1024"))
        assert cache_kib in (1024,3072)
        db.execute(f"PRAGMA cache_size={-cache_kib}")
        db.execute("PRAGMA wal_autocheckpoint=0")
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        connections.append((part, db))
    barrier.wait()
    timings = {k:[] for k in ("transaction","commit","checkpoint")}
    start = time.monotonic_ns()
    cpu_start = time.thread_time_ns()
    for sweep, needle in enumerate(manifest["needles"][:3],1):
        for part, db in connections:
            expected = reference[part["file"]]
            begin = time.monotonic_ns()
            db.execute("BEGIN IMMEDIATE")
            keys = db.execute("SELECT rowid FROM source WHERE rowid%8=0 AND length(body)>0").fetchall()
            assert len(keys) == expected["changed"]
            for (key,) in keys:
                with db.blobopen("source","body",key) as blob:
                    blob.write(bytes([sweep]))
            precommit = time.monotonic_ns()
            db.execute("COMMIT")
            committed = time.monotonic_ns()
            timings["transaction"].append(committed-begin)
            timings["commit"].append(committed-precommit)
            hits = db.execute("SELECT sum(instr(body,?)>0) FROM source",(needle.encode(),)).fetchone()[0]
            assert hits == expected["hits"][sweep-1]
            precheck = time.monotonic_ns()
            assert db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0,0,0)
            timings["checkpoint"].append(time.monotonic_ns()-precheck)
    stop = time.monotonic_ns()
    cpu_ns = time.thread_time_ns()-cpu_start
    barrier.wait()
    for part, db in connections:
        # Verify an aborted write, then reopen and hash all committed records.
        db.execute("BEGIN IMMEDIATE")
        key = db.execute("SELECT rowid FROM source WHERE length(body)>0 LIMIT 1").fetchone()[0]
        with db.blobopen("source","body",key) as blob:
            blob.write(b"!")
        db.execute("ROLLBACK")
        db.close()
        check = sqlite3.connect((folder/part["file"]).as_uri()+"?mode=ro", uri=True)
        digest = hashlib.sha256()
        for path,body in check.execute("SELECT path,body FROM source ORDER BY rowid"):
            for value in (path.encode(),body):
                digest.update(len(value).to_bytes(8,"little"))
                digest.update(value)
        check.close()
        assert digest.hexdigest() == reference[part["file"]]["sha256"]
    return start,stop,cpu_ns,timings

assert set(os.sched_getaffinity(0)) == set(range(4))
def io_counts():
    fields=dict(line.split(": ") for line in Path("/proc/self/io").read_text().splitlines())
    return {key:int(fields[key]) for key in ("read_bytes","write_bytes","cancelled_write_bytes")}

initial_io=io_counts()
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    workers = list(pool.map(writer,range(4)))
print(json.dumps(dict(sqlite=sqlite3.sqlite_version, verified_files=len(parts),
    io_bytes={k:v-initial_io[k] for k,v in io_counts().items()},
    db_bytes=sum(p["db_bytes"] for p in parts), cpu_ns=sum(w[2] for w in workers),
    wall_ns=max(w[1] for w in workers)-min(w[0] for w in workers),
    latency_ns={k:[v for w in workers for v in w[3][k]] for k in workers[0][3]})))
