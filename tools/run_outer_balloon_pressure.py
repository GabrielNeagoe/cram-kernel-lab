"""Run the existing guest balloon probe at a controlled Windows memory level."""
import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import time

import psutil
import run_outer_host_pressure as pressure


def run(args):
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    ssh = [args.plink, '-batch', '-ssh', args.destination, '-P', '22', '-i', args.key]
    script = ('/srv/cram-lab/outer-targeted-20261105/test_outer_targeted.py' if args.targeted
              else '/srv/cram-lab/outer-balloon-20261105/test_pressure_balloon.py')
    unit = 'cram-targeted-pressure' if args.targeted else 'cram-balloon-pressure'
    if args.diagnostic:
        assert args.targeted
        script='/srv/cram-lab/outer-targeted-diagnostic-20261105/test_outer_targeted.py'
    start_command = (f'sudo -n systemd-run --quiet --collect --pipe --wait --unit={unit} '
                     f'-p RuntimeMaxSec=420 -p "ExecStopPost=/usr/bin/python3 {script} --restore" '
                     f'/usr/bin/python3 {script} --pressure-run')
    if args.trace_frames:
        start_command += ' --trace-frames'
    if args.run_name:
        assert args.targeted and args.run_name.replace('-','').isalnum()
        start_command += ' --run-name '+args.run_name
    if args.diagnostic:
        start_command += ' --diagnostic --blockwise'
    vms = [p for p in psutil.process_iter(['name']) if p.info['name']=='vmware-vmx.exe']
    assert len(vms)==1
    vm = vms[0]
    cap = args.cap_gib*pressure.GIB
    pressure.safe(pressure.memory())
    if pressure.memory()['available'] < 32*pressure.GIB:
        raise RuntimeError('Insufficient headroom to start pressure ramp')
    helper = subprocess.Popen([sys.executable,__file__,'--allocator',str(args.cap_gib)],stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,text=True,bufsize=1)
    reply = pressure.channel(helper)
    guest = None
    allocated = 0
    index = 0
    stage = 'initial'
    started = time.monotonic()
    def adjust(action):
        nonlocal allocated
        pressure.send(helper,action)
        allocated=int(reply.get(timeout=8))
    with (output/'windows-samples.jsonl').open('x',encoding='utf-8') as log:
        def write(**fields):
            print(json.dumps(dict(t=round(time.monotonic()-started,3),wall_time=time.time(),**fields)),file=log,flush=True)
        def sample():
            state=pressure.memory()
            pressure.safe(state)
            write(kind='sample',stage=stage,index=index,vmx_ws=vm.memory_info().rss,
                  allocated=allocated,helper_ws=psutil.Process(helper.pid).memory_info().rss if allocated else 0,**state)
            return state['available']
        def regulate(available):
            if available>pressure.TARGET+pressure.CHUNK and allocated<cap:
                adjust('add')
            elif available<pressure.TARGET-pressure.CHUNK and allocated:
                adjust('drop')
        try:
            sample()
            stage='ramp'
            while sample()>pressure.TARGET+pressure.CHUNK:
                if allocated>=cap or time.monotonic()-started>210:
                    raise RuntimeError('Pressure target out of reach')
                adjust('add')
                time.sleep(.20)
            stage='stabilize'
            for _ in range(40):
                regulate(sample())
                time.sleep(.25)
            guest=subprocess.Popen(ssh+[start_command],stdout=subprocess.PIPE,text=True,bufsize=1)
            events=pressure.channel(guest)
            while stage!='finished':
                try:
                    line=events.get_nowait()
                except queue.Empty:
                    regulate(sample())
                    if time.monotonic()-started>660:
                        raise TimeoutError('Guest experiment deadline exceeded')
                    time.sleep(.25)
                    continue
                if line is None:
                    raise RuntimeError('Guest service exited before finished marker')
                event=json.loads(line)
                stage=event['event']
                index=event.get('index',index)
                write(kind='marker',**event)
                print(json.dumps(event),flush=True)
            assert guest.wait(timeout=15)==0
        finally:
            pressure.terminate(helper)
            allocated=0
            if guest is not None and guest.poll() is None:
                subprocess.run(ssh+['sudo -n systemctl stop '+unit],timeout=30,check=False)
                pressure.terminate(guest)
            stage='recovery'
            for _ in range(20):
                sample()
                time.sleep(.5)

if __name__=='__main__':
    if sys.argv[1:2]==['--allocator']:
        cap_gib=int(sys.argv[2])
        if cap_gib not in (88,92,96):
            raise ValueError('Unsupported allocation cap')
        pressure.allocator(cap_gib*pressure.GIB)
    else:
        parser=argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--targeted',action='store_true')
        parser.add_argument('--diagnostic',action='store_true')
        parser.add_argument('--run-name')
        parser.add_argument('--trace-frames',action='store_true')
        parser.add_argument('--cap-gib',type=int,choices=(88,92,96),default=88)
        for name in ('plink','destination','key','out'):
            parser.add_argument('--'+name,required=True)
        run(parser.parse_args())
