"""Sample the Windows VMware process while the guest holds matched test buffers."""
import argparse
import json
import queue
import shlex
import subprocess
import threading
import time
from pathlib import Path
from statistics import median
import psutil

parser = argparse.ArgumentParser()
for flag in ('plink','destination','key','guest-script','out'):
    parser.add_argument('--'+flag, required=True)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=False)
hosts = [p for p in psutil.process_iter(['name']) if p.info['name'].lower() == 'vmware-vmx.exe']
assert len(hosts) == 1, 'Expected exactly one running VMware process'
vm = hosts[0]
def sample_window(seconds):
    start = time.monotonic()
    samples = []
    while time.monotonic()-start < seconds:
        mem = vm.memory_info()
        samples.append(dict(t=round(time.monotonic()-start,3), vmx_ws=mem.rss,
                            windows_available=psutil.virtual_memory().available))
        time.sleep(.5)
    return samples
def read_guest(stream, messages):
    for line in stream:
        messages.put(line)
    messages.put(None)
def run(case, idx):
    quota = '0' if case=='disk' else '3M'
    cmd = ['systemd-run','--user','--quiet','--collect','--pipe','--wait','-p','MemoryMax=96M',
           '-p','MemorySwapMax=64M','-p','MemoryZSwapMax='+quota,'python3',args.guest_script,case]
    stages = {}
    raw = dict(case=case,guest=stages)
    outfile = out / f'{idx:02d}-{case}.json'
    with subprocess.Popen([args.plink,'-batch','-ssh',args.destination,'-P','22','-i',args.key,
                           shlex.join(cmd)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,text=True,bufsize=1) as p:
        q = queue.Queue()
        threading.Thread(target=read_guest,args=(p.stdout,q),daemon=True).start()
        def receive(expected):
            line = q.get(timeout=45)
            if line is None:
                raise RuntimeError('Guest ended early: '+p.stderr.read())
            item = json.loads(line)
            assert item['stage']==expected and item['case']==case
            stages[expected]=item
        def advance():
            p.stdin.write('go\n')
            p.stdin.flush()
        try:
            receive('filled')
            raw['filled_window'] = sample_window(6)
            advance()
            receive('after_action')
            raw['post_window'] = sample_window(16)
            advance()
            receive('settled')
            receive('verified')
            assert p.wait(timeout=15)==0
        finally:
            if p.poll() is None:
                p.kill()
            outfile.write_text(json.dumps(raw,indent=2)+'\n')
    before,held,end = (stages[s] for s in ('filled','settled','verified'))
    assert before['present_pages']==8192 and end['oom_kills']==0
    assert end['memory.cram.reserved']==0
    if case=='resident':
        assert held['present_pages']==8192 and held['swapped_pages']==0
    else:
        assert held['swapped_pages']>0
    if case=='arena':
        assert 0<held['memory.cram.reserved']<=3*1024**2
        assert held['zswpout']>before['zswpout'] and end['zswpin']>held['zswpin']
    if case=='disk':
        assert held['memory.cram.reserved']==0 and end['zswpout']==0
    delta = {}
    for metric in ('vmx_ws','windows_available'):
        pre = median(s[metric] for s in raw['filled_window'])
        post = median(s[metric] for s in raw['post_window'] if s['t']>=10)
        delta[metric+'_change_bytes'] = post-pre
    row = dict(case=case,trial=idx,guest_charge_change=held['memory.current']-before['memory.current'],
               swapped_pages=held['swapped_pages'],reserved=held['memory.cram.reserved'],
               zswpout=held['zswpout']-before['zswpout'],zswpin=end['zswpin']-held['zswpin'],**delta)
    print(json.dumps(row),flush=True)
    return row

order = ['resident','disk','arena','disk','arena','resident','arena','resident','disk']
rows=[]
for i,case in enumerate(order,1):
    rows.append(run(case,i))
    (out/'summary.json').write_text(json.dumps(dict(completed=len(rows),trials=rows),indent=2)+'\n')
