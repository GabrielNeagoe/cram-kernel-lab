"""Wait for a real host request; offer only a bounded pool of original frames.
Never writes the guest-forced offer parameter. Restore host policy before ending.
"""
import array,json,mmap,os,select,signal,struct,subprocess,sys,time
from pathlib import Path
import test_outer_balloon as b

BASE=Path('/srv/cram-lab/host-policy-20261106')
MODULE=Path('/srv/cram-lab/outer-targeted-diagnostic-20261105/build2/vmw_balloon.ko')
PROBE='/srv/cram-lab/outer-targeted-diagnostic-20261105/workload.py'
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
    if not b.PARAM.exists(): return dict(stock=True,counters=b.counters())
    requested=b.counters()['target']
    if requested:
        b.call('modprobe','-r','vmw_balloon')
        b.call('insmod',str(MODULE),'cram_blockwise=1')
        return dict(stock=False,needs_policy_restore=True,previous_target=requested,
                    explanation='Empty bounded driver retained to prevent uncontrolled inflation')
    return dict(stock=True,counters=b.restore())

def main():
    RUN.mkdir(exist_ok=False)
    worker=ballast=None
    result={}
    try:
        assert os.geteuid()==0 and os.uname().release==b.RELEASE
        params=Path('/sys/module/zswap/parameters')
        assert [(params/k).read_text().strip() for k in ('enabled','cram_arena_mb','cram_arena_fail_every')]==['Y','4','0'], 'Active 4 MiB CRAM boot required before any allocation'
        assert not b.PARAM.exists() and b.counters()['target']==b.counters()['current']==0
        assert not b.offline_frames()
        mem=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
        assert mem>24*1024**2
        b.call('modprobe','-r','vmw_balloon'); b.call('insmod',str(MODULE),'cram_blockwise=1')
        ballast=subprocess.Popen(['systemd-run','--quiet','--collect','--pipe','--wait',
            '-p','RuntimeMaxSec=900','-p','MemoryMax=11G','-p','MemorySwapMax=0','-p','MemoryZSwapMax=0',
            'python3',__file__,'--ballast'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        assert select.select([ballast.stdout],[],[],60)[0]
        assert ballast.stdout.readline().strip()=='ready'
        emit('ballast_ready',bytes=10*2**30,counters=b.counters())
        assert b.counters()['target']==0
        worker=subprocess.Popen(['systemd-run','--quiet','--collect','--pipe','--wait',
            '-p','RuntimeMaxSec=900','-p','MemoryMax=96M','-p','MemorySwapMax=64M','-p','MemoryZSwapMax=3M',
            'python3',PROBE,'arena','--trace-frames'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        assert select.select([worker.stdout],[],[],20)[0]
        filled=json.loads(worker.stdout.readline()); original=set(filled.pop('private_original_frames'))
        assert len(original)==8192
        worker.stdin.write('go\n');worker.stdin.flush()
        assert select.select([worker.stdout],[],[],20)[0]
        held=json.loads(worker.stdout.readline())
        assert held['swapped_pages']==8192 and held['memory.cram.reserved']==2*2**20
        data=array.array('Q',sorted(original)).tobytes()
        with open('/sys/kernel/debug/cram_target_frames','wb',buffering=0) as f: assert f.write(data)==len(data)
        stats={k:int(v) for k,v in (l.split(':') for l in b.DEBUG.read_text().splitlines() if l.startswith('lab_'))}
        acquired=stats['lab_acquired']; assert 0<acquired<=8192
        result.update(filled=filled,after_action=held,acquisition=stats)
        emit('ready_for_host_policy',acquired_pages=acquired,counters=b.counters())
        start=time.monotonic();first=None;last=None;samples=[];full=False
        while time.monotonic()-start<720:
            s=b.counters();samples.append(dict(time=time.time(),**s))
            assert 0<=s['current']<=acquired and not s['resetting']
            state=(s['target'],s['current'])
            if state!=last: emit('host_state',**s);last=state
            if s['target']>0 and s['current']==acquired and not full:
                offline=b.offline_frames();assert offline<=original and len(offline)==acquired
                first=time.monotonic();full=True
                emit('host_requested_full',original_frames_confirmed=acquired,**s)
            if first and time.monotonic()-first>=30 and not result.get('hold_complete'):
                assert b.offline_frames()<=original
                result['hold_complete']=True;emit('restore_original_policy_now',**s)
            if (RUN/'finish').exists():
                assert s['target']==s['current']==0, 'Restore host policy before finish'
                break
            time.sleep(1)
        assert b.counters()['target']==b.counters()['current']==0, 'Host policy still requests inflation'
        assert not b.offline_frames()
        result['samples']=samples
        result['cleanup']=safe_cleanup()
        worker.stdin.write('go\n');worker.stdin.flush(); assert worker.wait(timeout=20)==0
        result['settled']=json.loads(worker.stdout.readline()); verified=json.loads(worker.stdout.readline())
        assert verified['zswpin']-filled['zswpin']==8192 and verified['memory.cram.reserved']==verified['oom_kills']==0
        result.update(verified=verified,outcome='verified',host_requested_inflation=full)
    except BaseException as e:
        result.update(outcome='error',error=repr(e));raise
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
        a=mmap.mmap(-1,10*2**30,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS)
        a.madvise(mmap.MADV_NOHUGEPAGE)
        for i in range(0,len(a),4096): struct.pack_into('<Q',a,i,(i//4096+1)*0x9e3779b1)
        print('ready',flush=True);sys.stdin.readline();a.close()
    elif '--safe-cleanup' in sys.argv: print(json.dumps(safe_cleanup()))
    else:
        def stopped(*args): raise RuntimeError('Supervisor stopped')
        signal.signal(signal.SIGTERM,stopped)
        main()
