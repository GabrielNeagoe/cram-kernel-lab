"""Wait for a real host request; offer only a bounded pool of original frames.
Never writes the guest-forced offer parameter. Restore host policy before ending.
"""
import array,json,mmap,os,select,signal,struct,subprocess,sys,time
from pathlib import Path
import test_outer_balloon as b

BASE=Path('/srv/cram-lab/startup-balloon-20261106')
MODULE=BASE/'build/vmw_balloon.ko'
ALL_FRAMES=False
MARKERS=False
PROBE='/srv/cram-lab/outer-targeted-diagnostic-20261105/workload.py'
BALLAST_GIB=10
RUN=BASE/'run1'
if '--run-name' in sys.argv:
    name=sys.argv[sys.argv.index('--run-name')+1]
    assert name.replace('-','').isalnum()
    RUN=BASE/name

def emit(event,**data):
    r=dict(time=time.time(),event=event,**data)
    with (RUN/'events.jsonl').open('a') as f: print(json.dumps(r),file=f,flush=True)
    print(json.dumps(r),flush=True)

def safe_cleanup():
    # Keep stock balloon loading blocked until host policy is restored by cold boot.
    assert Path('/etc/modprobe.d/cram-startup-guard.conf').read_text()==GUARD
    if Path('/sys/module/vmw_balloon').exists():
        assert Path('/sys/module/vmw_balloon/parameters/cram_hold').exists()
        b.call('rmmod','vmw_balloon')
    assert not b.offline_frames()
    return dict(stock=False,driver_unloaded=True,startup_guard_retained=True)

GUARD='blacklist vmw_balloon\ninstall vmw_balloon /bin/false\n'

def measure_targeted(gate,original,acquired,result):
    emit('held_baseline_start',acquired_pages=acquired,counters=b.counters())
    time.sleep(30 if '--smoke' not in sys.argv else 5)
    assert b.counters()['current']==0 and not b.offline_frames()
    control='--hold-only' in sys.argv
    if not control: gate.write_text('0')
    emit('control_hold_start' if control else 'released_for_real_host_request',acquired_pages=acquired,counters=b.counters())
    start=time.monotonic();first=None;last=None;samples=[];full=False
    while time.monotonic()-start<(10 if '--smoke' in sys.argv else 60 if control else 180):
        s=b.counters();samples.append(dict(time=time.time(),**s))
        assert 0<=s['current']<=acquired and not s['resetting']
        state=(s['target'],s['current'])
        if state!=last: emit('host_state',**s);last=state
        if s['target']>0 and s['current']==acquired and not full:
            offline=b.offline_frames();assert offline<=original and len(offline)==acquired
            first=time.monotonic();full=True
            emit('host_requested_full',original_frames_confirmed=acquired,**s)
        if first and time.monotonic()-first>=60:
            assert b.offline_frames()<=original
            result['hold_complete']=True
            break
        time.sleep(1)
    final=b.counters()
    offline=b.offline_frames()
    assert offline<=original and len(offline)==final['current']
    if control: assert final['current']==0
    result['hold_only_control']=control
    result['offered_original_frames']=len(offline)
    emit('before_driver_unload',**final)
    result['samples']=samples
    return full

def main(measure=measure_targeted):
    RUN.mkdir(exist_ok=False)
    worker=ballast=None
    result={}
    try:
        assert os.geteuid()==0 and os.uname().release==b.RELEASE
        params=Path('/sys/module/zswap/parameters')
        assert [(params/k).read_text().strip() for k in ('enabled','cram_arena_mb','cram_arena_fail_every')]==['Y','4','0'], 'Active 4 MiB CRAM boot required before any allocation'
        assert not Path('/sys/module/vmw_balloon').exists()
        assert Path('/etc/modprobe.d/cram-startup-guard.conf').read_text()==GUARD
        assert not b.offline_frames()
        mem=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
        assert mem>24*1024**2
        b.call('insmod',str(MODULE),'cram_blockwise=1')
        gate=Path('/sys/module/vmw_balloon/parameters/cram_hold')
        assert gate.read_text().strip()=='Y'
        ballast=subprocess.Popen(['systemd-run','--quiet','--collect','--pipe','--wait',
            '-p','RuntimeMaxSec=900','-p',f'MemoryMax={BALLAST_GIB+1}G','-p','MemorySwapMax=0','-p','MemoryZSwapMax=0',
            'python3',__file__,'--ballast','--ballast-gib',str(BALLAST_GIB)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        assert select.select([ballast.stdout],[],[],60)[0]
        assert ballast.stdout.readline().strip()=='ready'
        emit('ballast_ready',bytes=BALLAST_GIB*2**30,counters=b.counters())
        assert b.counters()['current']==0
        worker=subprocess.Popen(['systemd-run','--quiet','--collect','--pipe','--wait',
            '-p','RuntimeMaxSec=900','-p','MemoryMax=96M','-p','MemorySwapMax=64M','-p','MemoryZSwapMax=3M',
            'python3',PROBE,'arena','--trace-frames']+(['--marker-dir',str(RUN)] if MARKERS else [])+(['--all-frames'] if ALL_FRAMES else []),stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        assert select.select([worker.stdout],[],[],60 if ALL_FRAMES else 20)[0]
        filled=json.loads(worker.stdout.readline()); original=set(filled.pop('private_original_frames'))
        result['filled']=filled
        assert len(original)==8192
        worker.stdin.write('go\n');worker.stdin.flush()
        assert select.select([worker.stdout],[],[],60 if ALL_FRAMES else 20)[0]
        held=json.loads(worker.stdout.readline())
        result['after_action']=held
        assert held['swapped_pages']==8192 and held['memory.cram.reserved']==2*2**20, 'Incomplete 32 MiB page-out or entry reservation'
        data=array.array('Q',sorted(original)).tobytes()
        with open('/sys/kernel/debug/cram_target_frames','wb',buffering=0) as f: assert f.write(data)==len(data)
        stats={k:int(v) for k,v in (l.split(':') for l in b.DEBUG.read_text().splitlines() if l.startswith('lab_'))}
        acquired=stats['lab_acquired']; assert 0<acquired<=8192
        result.update(filled=filled,after_action=held,acquisition=stats)
        full=measure(gate,original,acquired,result)
        result['cleanup']=safe_cleanup()
        emit('driver_unloaded')
        worker.stdin.write('go\n');worker.stdin.flush(); assert worker.wait(timeout=20)==0
        result['settled']=json.loads(worker.stdout.readline()); verified=json.loads(worker.stdout.readline())
        assert verified['zswpin']-filled['zswpin']==8192 and verified['memory.cram.reserved']==verified['oom_kills']==0
        result.update(verified=verified,outcome='verified',host_requested_inflation=full)
    except BaseException as e:
        import traceback
        frame=traceback.extract_tb(e.__traceback__)[-1]
        result.update(outcome='error',error=repr(e),failed_check=frame.line,failed_line=frame.lineno);raise
    finally:
        for proc in (worker,ballast):
            if proc and proc.poll() is None:
                try: proc.stdin.close();proc.wait(timeout=20)
                except Exception: proc.kill()
        result['cleanup']=safe_cleanup()
        (RUN/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        emit('finished',outcome=result.get('outcome'),cleanup=result['cleanup'])

if __name__=='__main__':
    if '--ballast' in sys.argv:
        n=int(sys.argv[sys.argv.index('--ballast-gib')+1]) if '--ballast-gib' in sys.argv else 10
        assert n in (6,10)
        a=mmap.mmap(-1,n*2**30,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS)
        a.madvise(mmap.MADV_NOHUGEPAGE)
        for i in range(0,len(a),4096): struct.pack_into('<Q',a,i,(i//4096+1)*0x9e3779b1)
        print('ready',flush=True);sys.stdin.readline();a.close()
    elif '--safe-cleanup' in sys.argv: print(json.dumps(safe_cleanup()))
    else:
        def stopped(*args): raise RuntimeError('Supervisor stopped')
        signal.signal(signal.SIGTERM,stopped)
        main()
