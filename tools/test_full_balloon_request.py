"""Compare a full real host request with a held control; never force a target."""
import argparse, json, os, signal, time
from pathlib import Path
import test_startup_host_request as lab

CAP=4*2**30//4096
def original_offline(pages):
    with open('/proc/kpageflags','rb') as f:
        return sum(bool(int.from_bytes(os.pread(f.fileno(),8,8*p),'little') & (1<<23)) for p in pages)

def measure(gate,original,acquired,result):
    samples=result['samples']=[]
    def sample(phase):
        s=lab.b.counters()
        avail=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
        samples.append(dict(time=time.time(),phase=phase,available_kib=avail,**s))
        assert 0<=s['current']<=CAP and avail>=10*1024**2 and not s['resetting'], 'guest safety guard'
        return s
    if args.preback:
        assert gate.read_text().strip()=='Y' and sample('preparing')['current']==0
        lab.emit('prepare_existing_backing');gate.with_name('cram_preback').write_text('1')
        assert 'lab_pending: '+str(CAP)+'\n' in lab.b.DEBUG.read_text()
        assert sample('prepared')['current']==0
    result.update(preback=args.preback,ballast_gib=lab.BALLAST_GIB)
    lab.emit('baseline',acquired=acquired)
    for elapsed in range(300 if args.physical_window else 30):
        if args.physical_window and elapsed>=30 and (lab.RUN/'baseline-finished').exists(): break
        assert sample('baseline')['current']==0
        time.sleep(1)
    deflated=gate.read_text().strip()=='Y' and not lab.b.offline_frames()
    assert deflated
    control=args.hold_only
    result.update(cap_bytes=4*2**30,hold_only_control=control)
    if not control: gate.write_text('0')
    lab.emit('control' if control else 'release')
    start=time.monotonic();first=None; exact=0;original_check=None
    while time.monotonic()-start < (320 if args.physical_window else (75 if control else 150)):
        s=sample('control' if control else 'open')
        if control: assert s['current']==0
        if s['current']==s['target'] and s['target']>0:
            exact+=1
            if first is None:
                first=time.monotonic(); original_check=original_offline(original)
                if lab.MARKERS:
                    frames = json.loads((lab.RUN/'cohort').read_text())['frames']
                    result['offline_indices_first'] = [i for i,p in enumerate(frames) if original_offline([p])]
                lab.emit('exact_match',original_offline=original_check,**s)
        held_since= start if control else first
        if held_since is not None and time.monotonic()-held_since>=75:
            if not args.physical_window or (lab.RUN/'hold-finished').exists(): break
        time.sleep(1)
    result.update(exact_positive_samples=exact,original_offline_at_first_match=original_check,
                  original_offline_before_deflation=original_offline(original))
    if lab.MARKERS:
        frames = json.loads((lab.RUN/'cohort').read_text())['frames']
        result['marker_count']=len(frames)
        result['offline_indices_final'] = [i for i,p in enumerate(frames) if original_offline([p])]
        (lab.RUN/'cohort').unlink()
    gate.write_text('1');lab.emit('close')
    deadline=time.monotonic()+60
    while sample('deflating')['current']:
        assert time.monotonic()<deadline,'balloon did not deflate'
        time.sleep(1)
    assert not lab.b.offline_frames(); lab.emit('deflated')
    for _ in range(25):
        assert sample('deflated')['current']==0
        time.sleep(1)
    return bool(exact)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-name',required=True);p.add_argument('--hold-only',action='store_true');p.add_argument('--preback',action='store_true');p.add_argument('--all-frames',action='store_true');p.add_argument('--physical-window',action='store_true');p.add_argument('--markers',action='store_true');args=p.parse_args()
    assert args.run_name.isalnum() and os.sysconf('SC_PAGE_SIZE')==4096
    lab.MARKERS=args.markers
    lab.ALL_FRAMES=args.all_frames
    lab.BASE=Path(__file__).resolve().parent;lab.RUN=lab.BASE/args.run_name;lab.MODULE=lab.BASE/'build/vmw_balloon.ko'
    lab.PROBE=str(lab.BASE/'full-request-workload.py')
    if args.preback:
        lab.MODULE=lab.BASE/'prebuild/vmw_balloon.ko';lab.BALLAST_GIB=6
    def stop(*unused): raise RuntimeError('supervisor stopped')
    signal.signal(signal.SIGTERM,stop)
    lab.main(measure)
