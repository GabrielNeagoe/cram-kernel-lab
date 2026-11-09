"""Sample per-worker reclaim events in a private tracefs instance on x86-64."""
import os
from contextlib import ExitStack
from pathlib import Path
import re
import time

BASE = Path("/sys/kernel/tracing")
EVENT = "cram_sqlite_probe/writeout"


def command(path, value, append=False):
    fd=os.open(path, os.O_WRONLY | (os.O_APPEND if append else 0))
    try:
        os.write(fd,(value+"\n").encode())
    finally:
        os.close(fd)


def histogram(path):
    rows = []
    dropped = None
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("{"):
            rows.append({k:int(v,0) for k,v in re.findall(r"(\w+):\s*(0x[0-9a-fA-F]+|[0-9]+)",line)})
        if line.strip().startswith("Dropped:"):
            dropped = int(line.split(":")[1])
    assert dropped == 0, dropped
    return rows


def sample(pid, workers, debug, detail=False):
    tids = [int(t.name) for t in Path(f"/proc/{pid}/task").iterdir()]
    assert all(t in {int(x) for x in (workers/"cgroup.threads").read_text().split()} for t in tids)
    predicate = " if " + " || ".join(f"common_pid == {t}" for t in tids)
    instance = BASE/"instances"/"cram_sqlite_reclaim"
    def counters():
        return {name:int((debug/("arena_"+name)).read_text()) for name in
                ("stores","loads","reject_poor","reject_full","reject_quota","spill_tries","spill_stores")}
    with ExitStack() as stack:
        command(BASE/"kprobe_events","p:"+EVENT+" swap_writeout folio=%si:x64",True)
        stack.callback(command, BASE/"kprobe_events", "-:"+EVENT, True)
        instance.mkdir()
        stack.callback(instance.rmdir)
        command(instance/"tracing_on","0")
        events = instance/"events"
        specs = [(events/"vmscan/mm_vmscan_lru_isolate","hist:keys=lru:vals=nr_scanned,nr_taken"),
                 (events/"vmscan/mm_vmscan_lru_shrink_inactive",
                  "hist:keys=reclaim_flags:vals=nr_scanned,nr_reclaimed"),
                 (events/EVENT,"hist:keys=folio:size=65536")]
        if detail:
            specs[1]=(specs[1][0],"hist:keys=reclaim_flags:vals=nr_activate0,nr_ref_keep")
        for event, spec in specs:
            try:
                command(event/"trigger",spec+predicate)
            except OSError as exc:
                raise ValueError((spec,(instance/"error_log").read_text())) from exc
            stack.callback(command,event/"trigger","!"+spec+predicate)
        before=counters()
        start=time.monotonic()
        # Histogram triggers run independently of ring-buffer recording.
        time.sleep(3)
        after=counters()
        tables=[]
        for event, spec in specs:
            command(event/"trigger",spec+":pause"+predicate)
            tables.append(histogram(event/"hist"))
        counts=[r["hitcount"] for r in tables[2]]
        return dict(seconds=round(time.monotonic()-start,3),worker_threads=len(tids),
                    isolate=tables[0],shrink=tables[1],writeout_calls=sum(counts),
                    distinct_folios=len(counts),repeated_folios=sum(n>1 for n in counts),
                    max_attempts_per_folio=max(counts,default=0),
                    arena_delta={k:after[k]-v for k,v in before.items()})
