"""Matched repeated Windows pressure ramps with the existing 32 MiB guest probe."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading
import time
from statistics import median
import psutil

GIB = 1024**3
MIB = 1024**2
CHUNK = 256*MIB
TARGET = 12*GIB
FLOOR = 8*GIB
CAP = 64*GIB
HOLD = 120

class PerformanceInfo(ctypes.Structure):
    _fields_ = [('cb',wintypes.DWORD)] + [(k,ctypes.c_size_t) for k in
                ('committed','limit','peak','physical','available','cache','kernel',
                 'kernel_paged','kernel_nonpaged','page_size')] + [
                (k,wintypes.DWORD) for k in ('handles','processes','threads')]

kernel32 = ctypes.WinDLL('kernel32',use_last_error=True)
kernel32.CreateMemoryResourceNotification.restype = wintypes.HANDLE
kernel32.QueryMemoryResourceNotification.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)]
low_event = kernel32.CreateMemoryResourceNotification(0)
if not low_event:
    raise ctypes.WinError(ctypes.get_last_error())
get_performance = ctypes.WinDLL('psapi',use_last_error=True).GetPerformanceInfo

def memory():
    info = PerformanceInfo()
    if not get_performance(ctypes.byref(info),ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    low = wintypes.BOOL()
    if not kernel32.QueryMemoryResourceNotification(low_event,ctypes.byref(low)):
        raise ctypes.WinError(ctypes.get_last_error())
    return dict(available=int(info.available*info.page_size),
                commit_free=int((info.limit-info.committed)*info.page_size), low=bool(low.value))

def safe(state):
    if state['low'] or state['available'] < FLOOR or state['commit_free'] < 16*GIB:
        raise RuntimeError('Reached host memory stop threshold')

def allocator(cap):
    # Private random pages are touched by ordinary user-space writes. EOF from
    # the driver releases the arrays even if the driver is interrupted.
    import numpy as np
    rng = np.random.default_rng(34511)
    blocks = []
    for line in sys.stdin:
        action = line.strip()
        if action=='quit':
            return
        if action=='add':
            state = memory()
            safe(state)
            if len(blocks)*CHUNK < cap and state['available'] >= TARGET:
                blocks.append(rng.integers(0,2**64,size=CHUNK//8,dtype=np.uint64))
        elif action=='drop':
            if blocks:
                blocks.pop()
        else:
            raise ValueError(action)
        print(len(blocks)*CHUNK,flush=True)

def reader(stream,q):
    for line in stream:
        q.put(line)
    q.put(None)

def channel(process):
    q = queue.Queue()
    threading.Thread(target=reader,args=(process.stdout,q),daemon=True).start()
    return q

def send(process,line):
    process.stdin.write(line+'\n')
    process.stdin.flush()

def terminate(p):
    if p is None:
        return
    if p.poll() is None:
        p.kill()
    p.wait(timeout=15)

def run(args):
    vm, = [p for p in psutil.process_iter(['name']) if p.info['name']=='vmware-vmx.exe']
    output = Path(args.out)
    output.mkdir(parents=True,exist_ok=False)
    ssh = [args.plink,'-batch','-ssh',args.destination,'-P','22','-i',args.key]
    def guest_vmstat():
        txt = subprocess.check_output(ssh+["grep -E '^(nr_balloon_pages|balloon_inflate|balloon_deflate|pswpin|pswpout) ' /proc/vmstat"],text=True,timeout=8)
        return {k:int(v) for k,v in (line.split() for line in txt.splitlines())}
    rows = []
    cases = ('arena','resident') if args.second_pair else ('resident','arena','arena','resident')
    cap = args.allocation_cap_gib*GIB
    for index,case in enumerate(cases,3 if args.second_pair else 1):
        if memory()['available'] < 32*GIB:
            raise RuntimeError('Insufficient recovered host headroom to start another trial')
        rec = dict(case=case,trial=index,guest={},samples=[],vmstat_before=guest_vmstat(),
                   allocation_cap_bytes=cap)
        proc = helper = None
        allocated = 0
        start = time.monotonic()
        def sample(stage):
            state = memory()
            safe(state)
            r = dict(stage=stage,t=round(time.monotonic()-start,3),vmx_ws=vm.memory_info().rss,
                     allocated=allocated,helper_ws=psutil.Process(helper.pid).memory_info().rss if helper else 0,**state)
            rec['samples'].append(r)
            return r
        def guest(expected):
            line = gq.get(timeout=30)
            if line is None:
                raise RuntimeError('Guest ended early: '+proc.stderr.read())
            r = json.loads(line)
            assert r['stage']==expected and r['case']==case
            rec['guest'][expected]=r
        def adjust(action):
            nonlocal allocated
            send(helper,action)
            line = hq.get(timeout=8)
            if line is None:
                raise RuntimeError('Pressure allocator exited')
            allocated=int(line)
        def observe(stage,seconds):
            until = time.monotonic()+seconds
            while time.monotonic()<until:
                sample(stage)
                time.sleep(.5)
        try:
            cmd = ['systemd-run','--user','--quiet','--collect','--pipe','--wait','-p','MemoryMax=96M',
                   '-p','MemorySwapMax=64M','-p','MemoryZSwapMax=3M','python3',args.guest_script,case]
            proc = subprocess.Popen(ssh+[shlex.join(cmd)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,text=True,bufsize=1)
            gq = channel(proc)
            guest('filled')
            observe('filled',6)
            send(proc,'go')
            guest('after_action')
            observe('pre_pressure',5)
            helper = subprocess.Popen([sys.executable,__file__,'--allocator',str(cap)],stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,text=True,bufsize=1)
            hq = channel(helper)
            deadline=time.monotonic()+150
            while sample('ramp')['available']>TARGET+CHUNK and allocated<cap:
                if time.monotonic()>deadline:
                    raise RuntimeError('Pressure ramp deadline exceeded')
                adjust('add')
                time.sleep(.20)
            began=time.monotonic()
            while time.monotonic()-began < HOLD:
                state=sample('hold')
                if state['available']>TARGET+CHUNK and allocated<cap:
                    adjust('add')
                elif state['available']<TARGET-CHUNK and allocated:
                    adjust('drop')
                time.sleep(.5)
            send(proc,'go')
            guest('settled')
            guest('verified')
            assert proc.wait(timeout=10)==0
            send(helper,'quit')
            helper.wait(timeout=20)
            helper=None
            allocated=0
            observe('recovery',12)
            rec['vmstat_after']=guest_vmstat()
            filled,held,end=[rec['guest'][s] for s in ('filled','settled','verified')]
            assert filled['present_pages']==8192 and end['oom_kills']==0 and end['memory.cram.reserved']==0
            if case=='arena':
                assert held['swapped_pages']>0 and 0<held['memory.cram.reserved']<=3*MIB
                assert held['zswpout']>filled['zswpout'] and end['zswpin']>held['zswpin']
            else:
                assert held['present_pages']==8192 and held['swapped_pages']==0
            samples=rec['samples']
            def values(stage,key):
                return [s[key] for s in samples if s['stage']==stage]
            hold=[s for s in samples if s['stage']=='hold']
            late=[s for s in hold if s['t']>=hold[-1]['t']-20]
            ws_before=median(values('filled','vmx_ws'))
            fraction=sum(abs(s['available']-TARGET)<=GIB for s in hold)/len(hold)
            row=dict(trial=index,case=case,seconds_held=hold[-1]['t']-hold[0]['t'],
                     baseline_ws=ws_before,late_ws=median(s['vmx_ws'] for s in late),
                     ws_change=median(s['vmx_ws'] for s in late)-ws_before,
                     minimum_hold_ws_change=min(values('hold','vmx_ws'))-ws_before,
                     available_before=median(values('filled','available')),
                     available_hold=median(values('hold','available')),min_available=min(s['available'] for s in samples),
                     available_recovered=median(values('recovery','available')[-10:]),
                     peak_allocated=max(values('hold','allocated')),helper_hold_ws=median(values('hold','helper_ws')),
                     fraction_within_1gib_target=fraction,guest_charge_change=held['memory.current']-filled['memory.current'],
                     swapped=held['swapped_pages'],reserved=held['memory.cram.reserved'],loads=end['zswpin']-held['zswpin'],
                     balloon_page_change=rec['vmstat_after']['nr_balloon_pages']-rec['vmstat_before']['nr_balloon_pages'])
            assert fraction>=.9,'Target pressure not maintained'
            rows.append(row)
            rec['verified']=True
            (output/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
            print(json.dumps(row),flush=True)
        finally:
            terminate(helper)
            terminate(proc)
            (output/f'{index:02d}-{case}.json').write_text(json.dumps(rec,indent=2)+'\n')

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--allocator':
        cap = int(sys.argv[2])
        assert cap in (CAP,72*GIB)
        allocator(cap)
    else:
        parser=argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--allocation-cap-gib',type=int,choices=(64,72),default=64)
        parser.add_argument('--second-pair',action='store_true')
        for name in ('plink','destination','key','guest-script','out'):
            parser.add_argument('--'+name,required=True)
        run(parser.parse_args())
