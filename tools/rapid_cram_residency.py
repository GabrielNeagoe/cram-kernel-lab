"""Sample only the verified VMEM addresses. Record timings and exceptions to full residency."""
import ctypes
import json
import mmap
import time
from pathlib import Path

import numpy as np
import windows_vmx_pages as win
from track_cram_frame_residency import mapping, wait


def query(handle, buffer):
    if not win.p.QueryWorkingSetEx(handle, buffer.ctypes.data, buffer.nbytes):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer[:, 1] & 1


def check_response():
    # Use two fresh pages of our own allocation to check present/absent detection.
    with mmap.mmap(-1, 8192) as pages:
        pages[0] = 1
        base = ctypes.addressof(ctypes.c_char.from_buffer(pages))
        buffer = np.zeros((2, 2), dtype=np.uint64)
        buffer[:, 0] = [base, base + 4096]
        before = query(ctypes.c_void_p(-1), buffer).tolist()
        pages[4096] = 1
        after = query(ctypes.c_void_p(-1), buffer).tolist()
        assert before == [1, 0] and after == [1, 1], (before, after)


def record(private, folder, arm, stop, deadline):
    marker = private/(arm+'.json')
    wait(marker.exists,deadline,stop)
    h, base, filename = mapping()
    try:
        data = json.loads(marker.read_text())
        offsets = data['offsets']
        assert data['file']==filename and len(offsets)==len(set(offsets))==8192
        assert all(0<=v<32*2**30 and v%4096==0 for v in offsets)
        buf = np.zeros((8192, 2), dtype=np.uint64)
        buf[:,0] = np.asarray(offsets,dtype=np.uint64)+base
        with (folder/'rapid.jsonl').open('x') as f:
            while not stop.exists() and not (folder/'stop').exists() and time.monotonic()<deadline:
                begin = time.time_ns()
                valid = query(h,buf)
                end = time.time_ns()
                missing = np.flatnonzero(valid==0)
                bits = np.packbits((valid==0),bitorder='little').tobytes().hex() if len(missing) else ''
                print(json.dumps([begin,end,len(missing),bits],separators=(',',':')),file=f)
                time.sleep(.010)
    finally:
        win.k.CloseHandle(h)
