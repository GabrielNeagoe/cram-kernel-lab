"""Temporary VMware balloon offer on the experimental Ubuntu VM; run as root."""
import hashlib
import array
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

BASE = Path('/srv/cram-lab/outer-balloon-20261105')
MODULE = BASE/'build/vmw_balloon.ko'
PARAM = Path('/sys/module/vmw_balloon/parameters/cram_offer_32m')
DEBUG = Path('/sys/kernel/debug/vmmemctl')
PROBE = '/srv/cram-lab/arena-reservation-20261103/outer-host-residency.py'
RELEASE = '7.2.0-rc2-cram-vm-arena-r2+'
RUN = BASE/'run'
EVENTS = RUN/'events.jsonl'
TRACE = '--trace-frames' in sys.argv

def offline_frames():
    """Collect KPF_OFFLINE across guest System RAM. Raw PFNs stay in memory."""
    ram = re.findall(r'^\s*([0-9a-f]+)-([0-9a-f]+) : System RAM$',Path('/proc/iomem').read_text(),re.M)
    assert ram and os.sysconf('SC_PAGE_SIZE')==4096
    result=set()
    with open('/proc/kpageflags','rb') as flags:
        for begin,end in ram:
            first=(int(begin,16)+4095)//4096
            limit=(int(end,16)+1)//4096
            flags.seek(first*8)
            for base in range(first,limit,131072):
                chunk=array.array('Q')
                chunk.fromfile(flags,min(131072,limit-base))
                result.update(base+i for i,word in enumerate(chunk) if word & (1<<23))
    return result

def call(*args):
    return subprocess.check_output(args,text=True,timeout=20).strip()

def counters():
    text = DEBUG.read_text()
    values = {k.strip():int(v) for k,v in re.findall(r'^([^:\n]+):\s*(\d+)\s*$',text,re.M)}
    target_reads = re.findall(r'^target\s*:\s*(\d+) \((\d+) failed\)',text,re.M)
    assert 'target' in values and 'current' in values
    return dict(target=values['target'], current=values['current'],
                refused=values.get('lab_refusals',0),not_needed=values.get('lab_notneeded',0),
                target_queries=list(map(int,target_reads[0])) if target_reads else [],
                resetting=bool(re.search(r'^is resetting\s*:\s*y',text,re.M)))

def event(kind,**fields):
    message = json.dumps(dict(event=kind,guest_time=time.time(),**fields))
    with EVENTS.open('a') as f:
        print(message,file=f,flush=True)
    if '--pressure-run' in sys.argv:
        print(message,flush=True)

def restore():
    if PARAM.exists():
        PARAM.write_text('0')
        for _ in range(40):
            s=counters()
            if s['current']==s['target']:
                break
            time.sleep(.25)
        call('modprobe','-r','vmw_balloon')
    if not Path('/sys/module/vmw_balloon').exists():
        call('modprobe','vmw_balloon')
    assert DEBUG.exists() and not PARAM.exists()
    return counters()

def trial(idx,case):
    cmd=['systemd-run','--quiet','--collect','--pipe','--wait','-p','RuntimeMaxSec=110',
         '-p','MemoryMax=96M','-p','MemorySwapMax=64M','-p','MemoryZSwapMax=3M','python3',PROBE,case]
    if TRACE:
        cmd.append('--trace-frames')
    def receive(stage):
        if not select.select([proc.stdout],[],[],15)[0]:
            raise TimeoutError(stage)
        line=proc.stdout.readline()
        result=json.loads(line)
        assert result['stage']==stage and result['case']==case
        return result
    def advance():
        proc.stdin.write('go\n')
        proc.stdin.flush()
    with subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1) as proc:
        try:
            filled=receive('filled')
            if TRACE:
                original=set(filled.pop('private_original_frames'))
                assert len(original)==8192
            event('filled',index=idx,case=case)
            time.sleep(4)
            advance()
            held=receive('after_action')
            event('baseline',index=idx,case=case)
            time.sleep(7)
            base=counters()
            assert base['current']==base['target']==0 and not base['resetting']
            if TRACE:
                offline_before=offline_frames()
            PARAM.write_text('1')
            event('offer',index=idx,case=case)
            snapshots=[]
            reached=None
            start=time.monotonic()
            while time.monotonic()-start<47:
                s=counters()
                assert s['target']==0 and not s['resetting'],s
                snapshots.append(s)
                if s['current']==8192 and reached is None:
                    reached=time.monotonic()
                    event('inflated',index=idx,case=case)
                    if TRACE:
                        offline_full=offline_frames()
                        assert counters()['current']==8192
                if reached is not None and time.monotonic()-reached>=30:
                    break
                if reached is None and time.monotonic()-start>=8 and s['not_needed']>base['not_needed'] and not s['current']:
                    break
                time.sleep(.5)
            PARAM.write_text('0')
            event('deflate',index=idx,case=case)
            for _ in range(40):
                if counters()['current']==0:
                    break
                time.sleep(.25)
            assert counters()['current']==0
            if TRACE:
                assert reached is not None
                offline_after=offline_frames()
                added=offline_full-offline_before
                trace=dict(original_pages=len(original),offline_before=len(offline_before),
                           offline_at_full=len(offline_full),offline_after=len(offline_after),
                           newly_offline=len(added),original_in_newly_offline=len(original & added),
                           returned_online=len(added-offline_after),baseline_restored=offline_before==offline_after)
                assert len(added)==8192 and offline_before<=offline_full and offline_before==offline_after,trace
            if '--pressure-run' in sys.argv:
                event('deflated',index=idx,case=case)
                time.sleep(7)
            advance()
            # TextIO may prefetch both terminal lines. Wait for process exit then
            # read the buffered records instead of using select for the second.
            assert proc.wait(timeout=15)==0
            settled=json.loads(proc.stdout.readline())
            verified=json.loads(proc.stdout.readline())
            assert settled['stage']=='settled' and verified['stage']=='verified'
            assert verified['oom_kills']==0 and verified['memory.cram.reserved']==0
            if case=='arena':
                assert settled['memory.cram.reserved']>0 and verified['zswpin']>filled['zswpin']
            else:
                assert settled['present_pages']==8192
            result=dict(index=idx,case=case,filled=filled,after_action=held,settled=settled,verified=verified,
                        before=base,samples=snapshots,after=counters(),peak_accepted_pages=max(s['current'] for s in snapshots))
            if TRACE:
                result['frame_trace']=trace
            (RUN/f'{idx:02d}-{case}.json').write_text(json.dumps(result,indent=2)+'\n')
            event('trial_done',index=idx,case=case,peak=result['peak_accepted_pages'])
            return result['peak_accepted_pages']
        finally:
            PARAM.write_text('0')
            if proc.poll() is None:
                proc.kill()

def main():
    result={'version':os.uname().release,'trials':[]}
    attempted=False
    try:
        assert os.geteuid()==0 and os.uname().release==RELEASE
        zswap = Path('/sys/module/zswap/parameters')
        for name, expected in [('enabled','Y'),('cram_arena_mb','4'),('cram_arena_fail_every','0')]:
            if (zswap/name).read_text().strip()!=expected:
                raise RuntimeError('Experimental boot option required: '+name)
        assert MODULE.is_file() and not PARAM.exists()
        assert not (RUN/'guest-result.json').exists()
        assert int(re.search(r'MemAvailable:\s+(\d+)',Path('/proc/meminfo').read_text())[1])>8*1024**2
        assert Path('/sys/module/vmw_balloon/refcnt').read_text().strip()=='0'
        original=Path(call('modinfo','-n','vmw_balloon'))
        staged=Path('/srv/cram-lab/stage-cram-vm-arena-r2/lib/modules')/RELEASE/'kernel/drivers/misc/vmw_balloon.ko'
        h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        assert h(staged)==h(original)
        result.update(installed_module_sha256=h(original),probe_module_sha256=h(MODULE),
                      taint_before=int(Path('/proc/sys/kernel/tainted').read_text()))
        native=[]
        for _ in range(4):
            native.append(counters())
            time.sleep(1)
        result['native']=native
        assert all(s['target']==s['current']==0 and not s['resetting'] for s in native)
        assert native[-1]['target_queries'][0]>native[0]['target_queries'][0]
        event('native_checked',current=0,target=0)
        attempted=True
        call('modprobe','-r','vmw_balloon')
        call('insmod',str(MODULE))
        time.sleep(2)
        for i,case in enumerate(('arena','resident','resident','arena'),1):
            peak=trial(i,case)
            result['trials'].append(peak)
            if i==2 and not any(result['trials']):
                break
        result['outcome']='completed'
    except BaseException as e:
        result['outcome']='error'
        result['error']=repr(e)
        raise
    finally:
        try:
            if attempted:
                result['restored_driver']=restore()
                assert h(original)==result['installed_module_sha256']
        finally:
            result['taint_after']=int(Path('/proc/sys/kernel/tainted').read_text())
            (RUN/'guest-result.json').write_text(json.dumps(result,indent=2)+'\n')
            event('finished',outcome=result.get('outcome','unknown'))

if __name__=='__main__':
    if '--pressure-run' in sys.argv:
        RUN = BASE/('pressure-frames' if TRACE else 'pressure-run')
        if TRACE:
            PROBE=str(BASE/'outer-host-residency-frames.py')
        RUN.mkdir(exist_ok=False)
        EVENTS = RUN/'events.jsonl'
    if sys.argv[1:]==['--restore']:
        restore()
    else:
        main()
