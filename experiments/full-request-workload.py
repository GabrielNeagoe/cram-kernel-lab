"""Guest half of the matched VMware host working-set study (Linux x86-64)."""
import ctypes
import hashlib
import json
import mmap
import os
import struct
import sys
from pathlib import Path

PAGE = os.sysconf('SC_PAGE_SIZE')
SIZE = 32 * 1024**2
case = sys.argv[1]
assert case in ('resident', 'disk', 'arena') and PAGE == 4096
params = Path('/sys/module/zswap/parameters')
assert [(params / k).read_text().strip() for k in ('enabled', 'cram_arena_mb', 'cram_arena_fail_every')] == ['Y', '4', '0']
group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
quota = 0 if case == 'disk' else 3 * 1024**2
for name, expected in [('memory.max', 96*1024**2), ('memory.swap.max', 64*1024**2), ('memory.zswap.max', quota)]:
    assert int((group / name).read_text()) == expected, name

data = mmap.mmap(-1, SIZE, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
data.madvise(mmap.MADV_NOHUGEPAGE)
marker_dir = Path(sys.argv[sys.argv.index('--marker-dir')+1]) if '--marker-dir' in sys.argv else None
stride = 1 if '--all-frames' in sys.argv else 32
indices = range(0, SIZE // PAGE, stride)
for i in range(SIZE // PAGE):
    data[i*PAGE:(i+1)*PAGE] = struct.pack('<Q', i+1) * (PAGE//8)
if marker_dir:
    nonce = os.urandom(16)
    for i in indices:
        data[i*PAGE:i*PAGE+24] = nonce + struct.pack('<Q', i)
initial = hashlib.sha256(data).hexdigest()
address = ctypes.addressof(ctypes.c_char.from_buffer(data))
trace_frames = '--trace-frames' in sys.argv[2:]
original_frames = []

def pagemap():
    with open('/proc/self/pagemap', 'rb') as f:
        f.seek(address // PAGE * 8)
        return [v for (v,) in struct.iter_unpack('<Q', f.read(SIZE // PAGE * 8))]

def frame_numbers(entries):
    assert all(v & (1 << 63) for v in entries)
    return [v & ((1 << 55)-1) for v in entries]

def snapshot(stage):
    entries = pagemap()
    stat = dict(line.split() for line in (group/'memory.stat').read_text().splitlines())
    counters = {k:int(stat[k]) for k in ('zswpout', 'zswpin', 'zswpwb')}
    for k in ('memory.current','memory.swap.current','memory.cram.reserved'):
        counters[k] = int((group/k).read_text())
    counters['present_pages'] = sum((v>>63)&1 for v in entries)
    counters['swapped_pages'] = sum((v>>62)&1 for v in entries)
    counters['oom_kills'] = int(dict(line.split() for line in (group/'memory.events').read_text().splitlines())['oom_kill'])
    if trace_frames and stage=='filled':
        original_frames.extend(frame_numbers(entries))
        assert len(set(original_frames))==SIZE//PAGE and 0 not in original_frames
        counters['private_original_frames'] = original_frames
    if trace_frames and stage=='settled' and case=='resident':
        assert original_frames == frame_numbers(entries)
    print(json.dumps(dict(stage=stage, case=case, kernel=os.uname().release, **counters)), flush=True)

def wait_for_host():
    if sys.stdin.readline().strip() != 'go':
        raise RuntimeError('Host sampler disconnected')

if marker_dir:
    import time
    pfns = frame_numbers(pagemap())
    selected = [pfns[i] for i in indices]
    with (marker_dir/'cohort.tmp').open('x') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(dict(nonce=nonce.hex(), frames=selected), f)
    (marker_dir/'cohort.tmp').rename(marker_dir/'cohort')
    deadline = time.monotonic()+(50 if stride == 1 else 16)
    while not (marker_dir/'validated').exists():
        assert time.monotonic()<deadline, 'host did not validate workload markers'
        time.sleep(0.1)
    assert selected == frame_numbers(pagemap())[::stride]
snapshot('filled')
wait_for_host()
if trace_frames:
    assert original_frames == frame_numbers(pagemap())
if case != 'resident':
    data.madvise(getattr(mmap, 'MADV_PAGEOUT', 21))
# Allow bounded retries for pages still waiting for reclaim/LRU processing.
if case=='arena':
    import time
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        values=pagemap()
        if all(v & (1<<62) for v in values) and int((group/'memory.cram.reserved').read_text())==SIZE//PAGE*256: break
        time.sleep(.1)
        data.madvise(getattr(mmap,'MADV_PAGEOUT',21))
snapshot('after_action')
wait_for_host()
snapshot('settled')
assert hashlib.sha256(data).hexdigest() == initial
data.close()
snapshot('verified')

