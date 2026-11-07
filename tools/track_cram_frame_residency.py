"""Track validated synthetic pages in the VMX working set during the two trial arms."""
import argparse
import ctypes
import json
import os
import struct
import subprocess
import time
from pathlib import Path
import numpy as np
import windows_vmx_pages as win

PAGE = 4096

def wait(check, deadline, stop):
    while not stop.exists() and time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.25)
    raise TimeoutError('trial stopped or deadline expired')

def verify(a, name, folder, deadline, stop):
    ssh = [a.plink, '-batch', '-ssh', a.destination, '-i', a.key]
    run = '/srv/cram-lab/frame-study-20261107/' + a.prefix + name
    def fetch():
        result = subprocess.run(ssh + ['sudo -n cat ' + run + '/cohort'], capture_output=True, timeout=8)
        if result.returncode == 0:
            return json.loads(result.stdout)
    cohort = wait(fetch, deadline, stop)
    files = list(Path(a.vm_dir).glob('*.vmem'))
    assert len(files) == 1 and files[0].stat().st_size == 32*2**30
    nonce = bytes.fromhex(cohort['nonce'])
    frames = cohort['frames']
    assert len(nonce) == 16 and len(set(frames)) == len(frames) == a.count
    offsets = []
    with files[0].open('rb', buffering=0) as f:
        for index, frame in enumerate(frames):
            assert 0 < frame < 3*2**18 or 2**20 <= frame < 33*2**18
            offset = (frame - (2**18 if frame >= 2**20 else 0))*PAGE
            f.seek(offset)
            assert f.read(24) == nonce + struct.pack('<Q', index*(8192//a.count)), 'synthetic page identity mismatch'
            offsets.append(offset)
    private = Path(a.private)
    temp = private/(name+'.tmp')
    temp.write_text(json.dumps(dict(file=files[0].name, offsets=offsets)))
    temp.rename(private/(name+'.json'))
    subprocess.run(ssh+['sudo -n touch ' + run + '/validated'], check=True, timeout=8)

def mapping():
    pid, = [p.pid for p in win.psutil.process_iter(['name']) if p.info['name']=='vmware-vmx.exe']
    h = win.k.OpenProcess(0x410,False,pid)
    if not h: raise ctypes.WinError(ctypes.get_last_error())
    address = 0
    try:
        while True:
            info = win.MBI()
            if not win.k.VirtualQueryEx(h,address,ctypes.byref(info),ctypes.sizeof(info)): break
            address = (info.BaseAddress or 0)+info.RegionSize
            if info.State!=0x1000 or info.RegionSize!=32*2**30: continue
            path = ctypes.create_unicode_buffer(32768)
            win.p.GetMappedFileNameW(h,info.BaseAddress,path,len(path))
            if path.value.lower().endswith('.vmem') and info.AllocationBase==info.BaseAddress:
                return h, info.BaseAddress, Path(path.value).name
        raise ValueError('single complete VMEM view required')
    except BaseException:
        win.k.CloseHandle(h)
        raise

def capture(a,name,folder,deadline,stop):
    h,base,filename = mapping()
    buf = None
    try:
        with (folder/'windows-page-metadata.jsonl').open('x') as stream:
            while not stop.exists() and not (folder/'stop').exists() and time.monotonic()<deadline:
                t = time.time()
                row = win.snapshot()
                row['time'] = t
                marker = Path(a.private)/(name+'.json')
                if buf is None and marker.exists():
                    data = json.loads(marker.read_text())
                    assert filename==data['file'] and len(data['offsets'])==a.count
                    buf = np.zeros((a.count,2),dtype=np.uint64)
                    offsets = data['offsets']
                    assert len(set(offsets))==a.count and all(0<=v<32*2**30 and v%PAGE==0 for v in offsets)
                    buf[:,0] = np.array(offsets,dtype=np.uint64)+base
                if buf is not None:
                    row['frame_sample_time'] = time.time()
                    if not win.p.QueryWorkingSetEx(h,buf.ctypes.data,buf.nbytes):
                        raise ctypes.WinError(ctypes.get_last_error())
                    row['resident_bits'] = np.packbits((buf[:,1]&1).astype('uint8'),bitorder='little').tobytes().hex()
                print(json.dumps(row),file=stream,flush=True)
                time.sleep(.6)
    finally:
        win.k.CloseHandle(h)
    (folder/'stopped').touch()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--count',type=int,choices=[256,8192],default=256)
    parser.add_argument('mode',choices=['verify','capture'])
    for key in ['out','private','vm-dir','plink','destination','key','prefix']:
        parser.add_argument('--'+key,required=key in ['out','private'])
    a=parser.parse_args()
    out=Path(a.out)
    deadline=time.monotonic()+1800
    stop=out/'stop'
    if a.mode=='capture':
        h,_,_=mapping()
        win.k.CloseHandle(h)
        (out/'monitor-ready').touch()
    for name in ['full1','control']:
        folder=out/('host-pages-'+name)
        wait((folder/'start').exists,deadline,stop)
        (capture if a.mode=='capture' else verify)(a,name,folder,deadline,stop)

