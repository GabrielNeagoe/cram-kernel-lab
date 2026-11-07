"""Read Windows residency metadata; never read or fault in VMware page contents."""
import ctypes as c
from ctypes import wintypes as w
import json
import time
import numpy as np
import psutil

k=c.WinDLL('kernel32',use_last_error=True)
p=c.WinDLL('psapi',use_last_error=True)
class MBI(c.Structure):
    _fields_=[('BaseAddress',c.c_void_p),('AllocationBase',c.c_void_p),('AllocationProtect',w.DWORD),
              ('PartitionId',w.WORD),('RegionSize',c.c_size_t),('State',w.DWORD),('Protect',w.DWORD),('Type',w.DWORD)]
k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]; k.OpenProcess.restype=w.HANDLE
k.CloseHandle.argtypes=[w.HANDLE]
k.VirtualQueryEx.argtypes=[w.HANDLE,c.c_void_p,c.POINTER(MBI),c.c_size_t]; k.VirtualQueryEx.restype=c.c_size_t
p.GetMappedFileNameW.argtypes=[w.HANDLE,c.c_void_p,w.LPWSTR,w.DWORD]; p.GetMappedFileNameW.restype=w.DWORD
p.QueryWorkingSetEx.argtypes=[w.HANDLE,c.c_void_p,w.DWORD]; p.QueryWorkingSetEx.restype=w.BOOL

def snapshot(scan=True):
    vms=[v for v in psutil.process_iter(['name']) if v.info['name']=='vmware-vmx.exe']
    assert len(vms)==1 and c.sizeof(MBI)==48
    h=k.OpenProcess(0x400|0x10,False,vms[0].pid)
    if not h: raise c.WinError(c.get_last_error())
    start=time.monotonic(); regions=[]
    try:
        address=0
        while True:
            info=MBI()
            if not k.VirtualQueryEx(h,address,c.byref(info),c.sizeof(info)): break
            next_address=(info.BaseAddress or 0)+info.RegionSize
            assert next_address>address
            address=next_address
            if info.State!=0x1000 or info.RegionSize<16*2**20: continue
            name=c.create_unicode_buffer(32768)
            p.GetMappedFileNameW(h,info.BaseAddress,name,len(name))
            kind='vmem' if name.value.lower().endswith('.vmem') else ('other-file' if name.value else 'unnamed')
            row=dict(kind=kind,region_bytes=info.RegionSize,type=int(info.Type),protection=int(info.Protect))
            if scan:
                counts=dict(valid_pages=0,locked_pages=0,large_pages=0,shared_pages=0)
                for offset in range(0,info.RegionSize,65536*4096):
                    n=min(65536,(info.RegionSize-offset)//4096)
                    buf=np.zeros((n,2),dtype=np.uint64)
                    buf[:,0]=np.arange(n,dtype=np.uint64)*4096+info.BaseAddress+offset
                    if not p.QueryWorkingSetEx(h,buf.ctypes.data,buf.nbytes): raise c.WinError(c.get_last_error())
                    flags=buf[:,1]; valid=(flags&1)!=0
                    counts['valid_pages']+=int(np.count_nonzero(valid))
                    for label,bit in [('locked_pages',22),('large_pages',23),('shared_pages',15)]:
                        counts[label]+=int(np.count_nonzero(valid & ((flags&(1<<bit))!=0)))
                row.update(counts)
            regions.append(row)
        return dict(seconds=time.monotonic()-start,regions=regions,
                    totals={key:sum(r.get(key,0) for r in regions) for key in ['region_bytes','valid_pages','locked_pages','large_pages','shared_pages']})
    finally: k.CloseHandle(h)

if __name__=='__main__':
    import sys,argparse
    from pathlib import Path
    parser=argparse.ArgumentParser()
    parser.add_argument('--inventory',action='store_true')
    parser.add_argument('--watch',type=Path)
    parser.add_argument('--seconds',type=int,default=900)
    args=parser.parse_args()
    if args.watch:
        args.watch.mkdir(parents=True,exist_ok=True)
        started=time.monotonic()
        with (args.watch/'windows-page-metadata.jsonl').open('x',encoding='utf-8') as log:
            while time.monotonic()-started<args.seconds and not (args.watch/'stop').exists():
                try:
                    result=dict(time=time.time(),**snapshot())
                except Exception as e:
                    result=dict(time=time.time(),error=repr(e))
                print(json.dumps(result),file=log,flush=True)
                if 'error' in result: break
                time.sleep(2)
    else:
        print(json.dumps(snapshot(not args.inventory),indent=2))
