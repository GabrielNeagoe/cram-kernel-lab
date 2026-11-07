"""Observe native VMware balloon requests during reversible host-policy tests."""
import argparse,json,subprocess,time
from pathlib import Path
import psutil
import run_outer_host_pressure as pressure

p=argparse.ArgumentParser()
for name in ('plink','destination','key','out'): p.add_argument('--'+name,required=True)
p.add_argument('--seconds',type=int,default=900)
p.add_argument('--allow-targeted',action='store_true')
p.add_argument('--allow-unloaded',action='store_true')
a=p.parse_args(); out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
ssh=[a.plink,'-batch','-ssh',a.destination,'-P','22','-i',a.key]
remote='''import json,re,time
from pathlib import Path
debug=Path('/sys/kernel/debug/vmmemctl')
loaded=debug.exists()
assert loaded or ALLOW_UNLOADED
s=debug.read_text() if loaded else 'target: 0\\ncurrent: 0'
d={k.strip():int(v) for k,v in re.findall(r'^([^:\\n]+):\\s*(\\d+)\\s*$',s,re.M)}
m=Path('/proc/meminfo').read_text()
gate=Path('/sys/module/vmw_balloon/parameters/cram_hold')
print(json.dumps(dict(guest_time=time.time(),hold=gate.read_text().strip() if gate.exists() else None,driver_loaded=loaded,target=d['target'],current=d['current'],guest_available_kib=int(re.search(r'MemAvailable:\\s+(\\d+)',m)[1]),guest_forced_offer_present=Path('/sys/module/vmw_balloon/parameters/cram_offer_32m').exists())))
'''
remote=remote.replace('ALLOW_UNLOADED',repr(a.allow_unloaded))
vm=[v for v in psutil.process_iter(['name']) if v.info['name']=='vmware-vmx.exe'];assert len(vm)==1
start=time.monotonic();last=None
with (out/'samples.jsonl').open('x',encoding='utf-8') as f:
    while time.monotonic()-start<a.seconds and not (out/'stop').exists():
        result=subprocess.run(ssh+['sudo -n python3 -'],input=remote,text=True,capture_output=True,timeout=15)
        if result.returncode: raise RuntimeError('Guest sampling failed: '+result.stderr)
        guest=json.loads(result.stdout)
        assert a.allow_targeted or not guest['guest_forced_offer_present'], 'Native driver required'
        mem=vm[0].memory_info()
        item=dict(time=time.time(),elapsed=time.monotonic()-start,vmx_ws=mem.rss,vmx_private_bytes=mem.private,**pressure.memory(),**guest)
        print(json.dumps(item),file=f,flush=True)
        state=(guest['target'],guest['current'])
        if state!=last:
            print(json.dumps(dict(event='balloon_state',**guest)),flush=True);last=state
        if guest['guest_available_kib']<8*1024**2:
            print('Guest memory guard reached; restore original host policy.',flush=True);break
        time.sleep(2)
print('Observation finished; no host policy was changed by this observer.',flush=True)
