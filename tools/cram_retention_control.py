"""Retain a stratified half of file offsets in the observer, and speed up inflation drains."""
import contextlib
import ctypes as C
import json
import mmap
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from cram_working_set_events import Watch
from track_cram_frame_residency import mapping, wait, win

COUNT=8192
HISTORIC='reentry-frames-01'

class Burst:
    def __init__(self,dll,offsets):
        self.fn=C.CDLL(str(dll.resolve())).drain_burst
        self.fn.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p,C.c_uint32,C.c_void_p,C.c_void_p,C.c_uint]
        self.fn.restype=C.c_uint32
        self.keys=np.zeros(COUNT,dtype=[('page','<u8'),('index','<u4'),('pad','<u4')])
        self.keys['page']=np.array(offsets,dtype=np.uint64)//4096
        self.keys['index']=np.arange(COUNT)
        self.keys.sort(order='page')
        self.hits=np.zeros(COUNT,dtype=np.uint32)
        self.totals=np.zeros(4,dtype=np.uint64)

    def drain(self,h,base):
        self.hits.fill(0);self.totals.fill(0)
        err=self.fn(h,base,self.keys.ctypes.data,COUNT,self.hits.ctypes.data,self.totals.ctypes.data,5)
        if err:raise C.WinError(err)
        i=np.flatnonzero(self.hits)
        hits=np.repeat(i,self.hits[i]).tolist()
        total,lost,within,_=self.totals.tolist()
        return total,lost,hits,within

def calibration(dll):
    fixture="""import ctypes as c,mmap,sys,time
with mmap.mmap(-1,8192*4096) as m:
 a=c.addressof(c.c_char.from_buffer(m));print(a,flush=True)
 sys.stdin.readline();time.sleep(.03);c.memset(a,7,8192*4096);time.sleep(.15)
"""
    result=[]
    for mode in ['previous','burst','burst','previous']:
        with subprocess.Popen([sys.executable,'-c',fixture],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) as child:
            addr=int(child.stdout.readline())
            h=win.k.OpenProcess(0x600,False,child.pid)
            if not h:raise C.WinError(C.get_last_error())
            try:
                w=Watch(h);burst=Burst(dll,range(0,COUNT*4096,4096))
                child.stdin.write('go\n');child.stdin.flush()
                found=set();lost=0
                end=time.monotonic()+.14
                while time.monotonic()<end:
                    if mode=='burst':
                        _,loss,hits,_=burst.drain(h,addr)
                    else:
                        time.sleep(.010)
                        entries,loss=w.drain()
                        hits=[(int(v)-addr)//4096 for v in entries if addr<=v<addr+COUNT*4096]
                    found.update(hits);lost+=loss
                result.append(dict(mode=mode,known=COUNT,observed_unique=len(found),reported_lost=lost))
            finally:win.k.CloseHandle(h)
    return result

def groups(out):
    path=out.parent/HISTORIC/'host-pages-full1'
    lo=json.loads((path/'physical-baseline.json').read_text())['end']
    hi=json.loads((path/'physical-hold.json').read_text())['start']
    historical=set()
    with (path/'reentry.jsonl').open() as f:
        for line in f:
            t0,t1,_,_,hits,_=json.loads(line)
            if lo<=t0<=t1<=hi:historical.update(hits)
    assert len(historical)==2048
    rng=random.Random(847921)
    pinned=set()
    for group in (historical,set(range(COUNT))-historical):
        pinned.update(rng.sample(sorted(group),len(group)//2))
    return historical,pinned

@contextlib.contextmanager
def lock_offsets(path,offsets):
    kernel=win.k
    h=C.c_void_p(-1)
    low=C.c_size_t();high=C.c_size_t();flags=C.c_uint32()
    if not kernel.GetProcessWorkingSetSizeEx(h,C.byref(low),C.byref(high),C.byref(flags)):
        raise C.WinError(C.get_last_error())
    if not kernel.SetProcessWorkingSetSizeEx(h,C.c_size_t(max(low.value,32<<20)),C.c_size_t(max(high.value,128<<20)),flags):
        raise C.WinError(C.get_last_error())
    try:
        with path.open('rb',buffering=0) as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as view:
            base=np.frombuffer(view,dtype=np.uint8,count=1).ctypes.data
            buf=np.zeros((len(offsets),2),dtype=np.uint64)
            buf[:,0]=base+np.asarray(offsets,dtype=np.uint64)
            acquired=[]
            def checked():
                if not win.p.QueryWorkingSetEx(h,buf.ctypes.data,buf.nbytes):raise C.WinError(C.get_last_error())
                valid_locked=1|(1<<22)
                assert np.all((buf[:,1]&valid_locked)==valid_locked),'file alias lost lock'
            try:
                for n in buf[:,0]:
                    a=C.c_void_p(int(n))
                    if not kernel.VirtualLock(a,C.c_size_t(4096)):raise C.WinError(C.get_last_error())
                    acquired.append(a)
                checked()
                yield checked
                checked()
            finally:
                for a in acquired:kernel.VirtualUnlock(a,C.c_size_t(4096))
    finally:
        if not kernel.SetProcessWorkingSetSizeEx(h,low,high,flags):raise C.WinError(C.get_last_error())

def record(a,name,folder,stop,deadline):
    pv=Path(a.private)
    marker=pv/(name+'.json')
    wait(marker.exists,deadline,stop)
    target=json.loads(marker.read_text())
    offsets=target['offsets']
    assert len(set(offsets))==COUNT and all(x%4096==0 and 0<=x<32*2**30 for x in offsets)
    main,base,filename=mapping()
    watch=None
    try:
        assert filename==target['file'] and Path(filename).name==filename
        if a.fast_only_hold:
            historic,pinned=groups(folder.parent)
            with lock_offsets(Path(a.vm_dir)/filename,[offsets[j] for j in sorted(pinned)]):
                (folder/'pinned.json').write_text(json.dumps(dict(
                    historic_indices=sorted(historic),pinned_indices=sorted(pinned))))
                while not stop.exists() and not (folder/'stop').exists() and time.monotonic()<deadline:
                    time.sleep(.25)
            (folder/'locks-released.json').write_text(json.dumps(dict(locked_at_start_and_end=len(pinned))))
            return
        win.k.GetProcessId.argtypes=[C.c_void_p]
        pid=win.k.GetProcessId(main)
        watch=win.k.OpenProcess(0x600,False,pid)
        if not watch:raise C.WinError(C.get_last_error())
        historic,pinned=groups(folder.parent)
        calibration=dict(historic_indices=sorted(historic),pinned_indices=sorted(pinned))
        with lock_offsets(Path(a.vm_dir)/filename,[offsets[j] for j in sorted(pinned)]) as check:
            (folder/'pinned.json').write_text(json.dumps(calibration))
            w=Watch(watch)
            burst=Burst(pv/'cram_burst.dll',offsets)
            pages={(base+x)//4096:i for i,x in enumerate(offsets)}
            t=time.time();fast_start=None;fast_end=None
            with (folder/'reentry.jsonl').open('x') as f:
                while not stop.exists() and not (folder/'stop').exists() and time.monotonic()<deadline:
                    if fast_start is None and (folder/'physical-baseline.json').exists():fast_start=t
                    if fast_start is not None and fast_end is None and (folder/'begin-hold').exists():fast_end=t+8
                    if fast_start is not None and t-fast_start<45 and (fast_end is None or t<fast_end):
                        total,lost,hits,within=burst.drain(watch,base)
                    else:
                        time.sleep(.010)
                        entries,lost=w.drain()
                        total=len(entries)
                        hits=[pages[n] for v in entries if (n:=int(v)//4096) in pages]
                        within=sum(base<=int(v)<base+32*2**30 for v in entries)
                    end=time.time()
                    print(json.dumps([t,end,total,lost,hits,within],separators=(',',':')),file=f)
                    t=end
            check()
        (folder/'locks-released.json').write_text(json.dumps(dict(locked_at_start_and_end=len(pinned),burst_start=fast_start,burst_end=min(fast_start+45,fast_end) if fast_start and fast_end else None)))
    finally:
        if watch:win.k.CloseHandle(watch)
        win.k.CloseHandle(main)
