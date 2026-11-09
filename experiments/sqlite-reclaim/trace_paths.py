"""Short, worker-filtered histograms for paths preceding folio activation."""
from contextlib import ExitStack
from pathlib import Path
from collections import Counter
import re
import time

from trace_sample import BASE, command


def sample(pid, workers, debug, detail=False):
    tids = [int(t.name) for t in Path(f"/proc/{pid}/task").iterdir()]
    assert set(tids) <= set(map(int,(workers/"cgroup.threads").read_text().split()))
    predicate = " if " + " || ".join(f"common_pid == {t}" for t in tids)
    instance=BASE/"instances"/"cram_sqlite_branches"
    probes=[
        ("folios","p","folio_referenced","folio=%di:x64","folio"),
        ("refs","r","folio_referenced","ret=$retval:s32","ret"),
        ("swap_alloc","r","folio_alloc_swap","ret=$retval:s32","ret"),
        ("remove","r","__remove_mapping","ret=$retval:s32","ret"),
    ]
    def residency():
        stats=dict(x.split() for x in (workers/"memory.stat").read_text().splitlines())
        swap=dict(x.split() for x in (workers.parent/"memory.swap.events").read_text().splitlines())
        return dict(anon=int(stats["anon"]),swapcached=int(stats["swapcached"]),
                    anon_thp=int(stats["anon_thp"]),unevictable=int(stats["unevictable"]),
                    inactive_anon=int(stats["inactive_anon"]),active_anon=int(stats["active_anon"]),
                    zswap=int(stats["zswap"]),swapped=int((workers.parent/"memory.swap.current").read_text()),
                    swap_charge_failures=int(swap["fail"]),swap_limit_hits=int(swap["max"]))
    result={"before":residency()}
    with ExitStack() as stack:
        for name,kind,func,args,_ in probes:
            event="cram_sqlite_paths/"+name
            # Size the return-probe pool explicitly for four vCPUs.
            prefix="r128" if kind=="r" else "p"
            command(BASE/"kprobe_events",f"{prefix}:{event} {func} {args}",True)
            stack.callback(command,BASE/"kprobe_events","-:"+event,True)
        instance.mkdir()
        stack.callback(instance.rmdir)
        command(instance/"tracing_on","0")
        entries=[]
        for name,_,_,_,key in probes:
            ev=instance/"events/cram_sqlite_paths"/name
            entries.append((name,ev,"hist:keys="+key+(":size=65536" if name=="folios" else "")))
        entries.append(("unmap_dirty",instance/"events/vmscan/mm_vmscan_lru_shrink_inactive",
                        "hist:keys=reclaim_flags:vals=nr_unmap_fail,"+("nr_writeback" if detail else "nr_dirty")))
        for name,ev,spec in entries:
            command(ev/"trigger",spec+predicate)
            stack.callback(command,ev/"trigger","!"+spec+predicate)
        t0=time.monotonic()
        stacks=Counter()
        for _ in range(10):
            time.sleep(.1)
            for tid in tids:
                try:
                    trace=Path(f"/proc/{tid}/stack").read_text()
                except (FileNotFoundError,ProcessLookupError):
                    continue
                names=re.findall(r"\] (\w+)\+",trace)
                if names:
                    stacks[" > ".join(names)] += 1
        result["stack_samples"]=dict(stacks)
        for _,ev,spec in entries:
            command(ev/"trigger",spec+":pause"+predicate)
        result["seconds"]=round(time.monotonic()-t0,3)
        for name,ev,_ in entries:
            text=(ev/"hist").read_text()
            assert re.search(r"Dropped:\s*0\b",text),name
            rows=[{k:int(v,0) for k,v in re.findall(r"(\w+):\s*(0x[0-9a-fA-F]+|-?[0-9]+)",line)}
                  for line in text.splitlines() if line.lstrip().startswith("{")]
            if name=="folios":
                hits=[r["hitcount"] for r in rows]
                result[name]=dict(visits=sum(hits),distinct=len(hits),revisited=sum(v>1 for v in hits),
                                  max_visits=max(hits,default=0))
            else:
                result[name]=rows
        names={probe[0] for probe in probes}
        profiles=[line.split() for line in (BASE/"kprobe_profile").read_text().splitlines()]
        result["probe_misses"]={name:int(misses) for name,_,misses in profiles if name in names}
        assert set(result["probe_misses"])==names
    result["after"]=residency()
    return result
