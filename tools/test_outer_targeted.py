"""Bounded exact-frame reacquisition: matched hold versus VMware offer."""
import array
import json
from pathlib import Path
import select
import subprocess
import sys
import time
import test_outer_balloon as b

b.BASE = Path('/srv/cram-lab/outer-targeted-20261105')
if '--diagnostic' in sys.argv:
    b.BASE=Path('/srv/cram-lab/outer-targeted-diagnostic-20261105')
b.MODULE = b.BASE/'build/vmw_balloon.ko'
if '--blockwise' in sys.argv or '--compare-acquisition' in sys.argv:
    b.MODULE=b.BASE/'build2/vmw_balloon.ko'
b.PROBE = str(b.BASE/'workload.py')
b.RUN = b.BASE/('smoke' if '--smoke' in sys.argv else 'matched')
if '--run-name' in sys.argv:
    name=sys.argv[sys.argv.index('--run-name')+1]
    assert name.replace('-','').isalnum()
    b.RUN=b.BASE/name
b.EVENTS = b.RUN/'events.jsonl'
FRAMES = Path('/sys/kernel/debug/cram_target_frames')

def pool():
    return {line.split(':')[0]:int(line.split(':')[1]) for line in b.DEBUG.read_text().splitlines()
            if line.startswith('lab_')}

def trial(index, ignored):
    mode = ('offer','hold','hold','offer')[index-1]
    # Fresh module releases hold-only pages between trials and resets statistics.
    b.restore()
    b.call('modprobe','-r','vmw_balloon')
    blockwise='--blockwise' in sys.argv or ('--compare-acquisition' in sys.argv and index>=3)
    b.call('insmod',str(b.MODULE), 'cram_blockwise=1' if blockwise else 'cram_blockwise=0')
    time.sleep(2)
    # Scan before allocating the workload: the scanner itself allocates memory.
    offline_before=b.offline_frames()
    assert not offline_before
    cmd=['systemd-run','--quiet','--collect','--pipe','--wait','-p','RuntimeMaxSec=150',
         '-p','MemoryMax=96M','-p','MemorySwapMax=64M','-p','MemoryZSwapMax=3M',
         'python3',b.PROBE,'arena','--trace-frames']
    with subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1) as proc:
        def receive(stage):
            assert select.select([proc.stdout],[],[],20)[0],stage
            result=json.loads(proc.stdout.readline())
            assert result['stage']==stage
            return result
        def advance():
            proc.stdin.write('go\n'); proc.stdin.flush()
        try:
            filled=receive('filled')
            original=set(filled.pop('private_original_frames'))
            assert len(original)==8192
            b.event('filled',index=index,case=mode)
            advance()
            held=receive('after_action')
            assert held['swapped_pages']==8192 and held['present_pages']==0,held
            assert held['memory.cram.reserved']==8192*256,held
            b.event('acquire',index=index,case=mode)
            started=time.monotonic()
            payload=array.array('Q',sorted(original)).tobytes()
            with FRAMES.open('wb',buffering=0) as f:
                assert f.write(payload)==len(payload)
            acquisition_seconds=time.monotonic()-started
            stats=pool()
            (b.RUN/f'{index:02d}-acquisition.json').write_text(json.dumps(dict(pool=stats,seconds=acquisition_seconds),indent=2)+'\n')
            acquired=stats['lab_acquired']
            assert acquired==stats['lab_pending'] and acquired>0 and acquired+stats['lab_failed']==8192,stats
            b.event('baseline',index=index,case=mode,acquired_pages=stats['lab_acquired'])
            time.sleep(7)
            base=b.counters()
            assert base['target']==base['current']==0
            if mode=='offer':
                b.PARAM.write_text('1')
                for _ in range(40):
                    if b.counters()['current']==acquired:
                        break
                    time.sleep(.25)
                assert b.counters()['current']==acquired
            offline_full=b.offline_frames()
            assert offline_full<=original and len(offline_full)==(acquired if mode=='offer' else 0)
            b.event('inflated',index=index,case=mode)
            samples=[]
            start=time.monotonic()
            while time.monotonic()-start<30:
                s=b.counters()
                assert s['target']==0 and s['current']==(acquired if mode=='offer' else 0) and not s['resetting'],s
                samples.append(s)
                time.sleep(.5)
            assert b.offline_frames()==offline_full
            b.PARAM.write_text('0')
            for _ in range(40):
                if b.counters()['current']==0: break
                time.sleep(.25)
            assert b.counters()['current']==0
            after=b.counters()
            b.restore()  # Also frees every hold-only frame through the allocator.
            assert not b.offline_frames()
            b.event('deflated',index=index,case=mode)
            time.sleep(7)
            advance()
            assert proc.wait(timeout=20)==0
            settled=json.loads(proc.stdout.readline())
            verified=json.loads(proc.stdout.readline())
            assert settled['stage']=='settled' and verified['stage']=='verified'
            assert settled['swapped_pages']==8192 and settled['memory.cram.reserved']==8192*256
            assert verified['zswpin']-filled['zswpin']==8192
            assert verified['memory.cram.reserved']==0 and verified['oom_kills']==0
            result=dict(index=index,case=mode,filled=filled,after_action=held,settled=settled,verified=verified,
                        acquisition_seconds=acquisition_seconds,pool=stats,before=base,samples=samples,after=after,
                        frame_trace=dict(original_pages=8192,acquired_pages=acquired,
                                         original_in_newly_offline=len(original & offline_full),
                                         newly_offline=len(offline_full),baseline_restored=True))
            (b.RUN/f'{index:02d}-{mode}.json').write_text(json.dumps(result,indent=2)+'\n')
            b.event('trial_done',index=index,case=mode,peak=max(s['current'] for s in samples))
            # The shared supervisor expects the temporary module present on return.
            b.call('modprobe','-r','vmw_balloon'); b.call('insmod',str(b.MODULE))
            return max(s['current'] for s in samples)
        finally:
            if b.PARAM.exists(): b.PARAM.write_text('0')
            if proc.poll() is None: proc.kill()

b.trial=trial
if __name__=='__main__':
    if '--restore' in sys.argv:
        b.restore()
    else:
        b.RUN.mkdir(exist_ok=False)
        b.main()
