"""Build immutable SQLite search indexes from tracked Linux C and header files."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

NEEDLES = ("mutex_lock", "return -EINVAL", "spin_lock", "EXPORT_SYMBOL", "kfree(", "struct device")
TARGET = 4 * 1024**2
repo, out = map(Path, sys.argv[1:])
out.mkdir(exist_ok=False)
commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
listing = subprocess.check_output(["git", "-C", repo, "ls-tree", "-r", "-z", commit])
files = []
for item in listing.split(b"\0"):
    if item:
        meta, path = item.split(b"\t", 1)
        if path.endswith((b".c", b".h")) and meta.startswith(b"100644 blob "):
            files.append((path, meta.split()[-1]))
files.sort(key=lambda item: hashlib.sha256(item[0]).digest())
reader = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE)
parts = []
con = None
for path, oid in files:
    reader.stdin.write(oid + b"\n")
    reader.stdin.flush()
    n = int(reader.stdout.readline().split()[-1])
    body = reader.stdout.read(n)
    assert reader.stdout.read(1) == b"\n" and len(body) == n
    if n > TARGET // 4:
        continue
    if con is None:
        filename = f"part-{len(parts):04}.db"
        con = sqlite3.connect(out / filename)
        con.execute("PRAGMA page_size=4096")
        con.execute("CREATE TABLE source(path TEXT PRIMARY KEY, body BLOB NOT NULL)")
        totals = [0] * len(NEEDLES)
        rows = volume = 0
    con.execute("INSERT INTO source VALUES (?,?)", (path.decode(), body))
    for j, needle in enumerate(NEEDLES):
        totals[j] += needle.encode() in body
    rows += 1
    volume += n
    if volume >= TARGET:
        con.commit()
        assert con.execute("PRAGMA quick_check").fetchone() == ("ok",)
        con.close()
        con = None
        parts.append(dict(file=filename, rows=rows, source_bytes=volume, hits=totals,
                          db_bytes=(out / filename).stat().st_size))
        if len(parts) == 256:
            break
reader.stdin.close()
assert reader.wait() == 0
if con is not None:
    con.close()
    (out / filename).unlink()
manifest = dict(linux_commit=commit, sqlite_version=sqlite3.sqlite_version, needles=NEEDLES, parts=parts)
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(len(parts), "complete partitions", sum(p["db_bytes"] for p in parts), "SQLite bytes")
