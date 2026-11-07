"""Count working-set additions only for marker-verified addresses in the test VM."""
import ctypes as C
import json
import mmap
import time
from pathlib import Path

import numpy as np
from track_cram_frame_residency import mapping, wait, win
from rapid_cram_residency import query

init=win.p.InitializeProcessForWsWatch
init.argtypes=[C.c_void_p]
changes=win.p.GetWsChangesEx
changes.argtypes=[C.c_void_p,C.c_void_p,C.POINTER(C.c_uint32)]


class Watch:
    def __init__(self, handle):
        self.handle=handle
        self.buf=np.zeros((8192,4),dtype=np.uint64)
        if not init(handle):
            raise C.WinError(C.get_last_error())

    def drain(self):
        size=C.c_uint32(self.buf.nbytes)
        if not changes(self.handle,self.buf.ctypes.data,C.byref(size)):
            code=C.get_last_error()
            if code==259:
                return (),0
            raise C.WinError(code)
        n=int(np.flatnonzero(self.buf[:,0]==0)[0])
        return self.buf[:n,1],int(self.buf[n,1])


def calibrate():
    h=C.c_void_p(-1)
    w=Watch(h)
    with mmap.mmap(-1,4096) as page:
        page[0]=7
        address=C.addressof(C.c_char.from_buffer(page))
        state=np.array([[address,0]],dtype=np.uint64)
        assert query(h,state)[0]==1
        return check_reentries(w,h,page,address,state)


def check_reentries(w,h,page,address,state):
    measured=[]
    for attempt in range(4):
        w.drain()
        # Unlock on an unlocked page removes this disposable page from our own working set.
        win.k.VirtualUnlock(C.c_void_p(address),C.c_size_t(4096))
        assert query(h,state)[0]==0
        assert page[0]==7
        entries,lost=w.drain()
        matches=sum(int(v)//4096==address//4096 for v in entries)
        if attempt:
            assert matches and not lost,(matches,lost)
            measured.append(matches)
    return dict(known_reentry_events=measured,lost=0)


def record(private,folder,arm,stop,deadline):
    marker=private/(arm+'.json')
    wait(marker.exists,deadline,stop)
    handle,base,filename=mapping()
    watch_handle=None
    try:
        win.k.GetProcessId.argtypes=[C.c_void_p]
        pid=win.k.GetProcessId(handle)
        assert pid
        watch_handle=win.k.OpenProcess(0x600,False,pid)
        if not watch_handle: raise C.WinError(C.get_last_error())
        target=json.loads(marker.read_text())
        assert target['file']==filename and len(set(target['offsets']))==8192
        pages={(base+v)//4096:i for i,v in enumerate(target['offsets'])}
        w=Watch(watch_handle)
        beg=time.time()
        with (folder/'reentry.jsonl').open('x') as log:
            while not stop.exists() and not (folder/'stop').exists() and time.monotonic()<deadline:
                entries,lost=w.drain()
                end=time.time()
                hits=[pages[n] for v in entries if (n:=int(v)//4096) in pages]
                within=sum(base<=int(v)<base+32*2**30 for v in entries)
                print(json.dumps([beg,end,len(entries),lost,hits,within],separators=(',',':')),file=log)
                beg=end
                time.sleep(.010)
    finally:
        if watch_handle:win.k.CloseHandle(watch_handle)
        win.k.CloseHandle(handle)

if __name__=='__main__':print(calibrate())
