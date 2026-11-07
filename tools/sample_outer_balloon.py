"""Record Windows VMware working set against guest balloon experiment markers."""
import argparse,json,queue,subprocess,threading,time
from pathlib import Path
import psutil

p=argparse.ArgumentParser()
for key in ('plink','destination','key','out'):
    p.add_argument('--'+key,required=True)
a=p.parse_args()
out=Path(a.out)
vm,=[p for p in psutil.process_iter(['name']) if p.info['name']=='vmware-vmx.exe']
remote='/srv/cram-lab/outer-balloon-20261105/run/events.jsonl'
q=queue.Queue()
with subprocess.Popen([a.plink,'-batch','-ssh',a.destination,'-P','22','-i',a.key,
                       'tail -n 0 -f -s 0.1 '+remote],stdout=subprocess.PIPE,text=True) as child:
    def listen():
        for line in child.stdout:
            q.put(json.loads(line))
    threading.Thread(target=listen,daemon=True).start()
    t=time.monotonic()
    stage='waiting'
    index=0
    try:
        with out.open('x',encoding='utf-8') as f:
            while time.monotonic()-t<1500 and child.poll() is None:
                while not q.empty():
                    ev=q.get()
                    stage=ev['event']
                    index=ev.get('index',index)
                    print(json.dumps(dict(kind='marker',t=time.monotonic()-t,**ev)),file=f,flush=True)
                if stage=='finished':
                    break
                if stage!='waiting':
                    print(json.dumps(dict(kind='sample',stage=stage,index=index,t=time.monotonic()-t,
                                          vmx_ws=vm.memory_info().rss,available=psutil.virtual_memory().available)),
                          file=f,flush=True)
                time.sleep(.25)
    finally:
        child.kill()
