"""Measure a standalone compressed store; never report these as CRAM savings."""
from pathlib import Path
import json
import re
import selectors
import subprocess
import time

base=Path(__file__).resolve().parent
binary=base/'compressed-store-prototype'
subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(base/'compressed-store-prototype.c'),'-lzstd','-o',str(binary)],check=True)
out=base/'store-results';out.mkdir(exist_ok=False)
results=[]
for repeat in range(3):
    for pattern in ('repeat','mixed','random'):
        p=subprocess.Popen([str(binary),pattern],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        lines=[];stages={}
        try:
            # This small process is bounded by the external command timeout;
            # checkpoints are synchronous and each has three host samples.
            for line in p.stdout:
                lines.append(line)
                if line.startswith('CHECKPOINT='):
                    samples=[]
                    for _ in range(3):
                        time.sleep(.1)
                        contents=Path(f'/proc/{p.pid}/smaps_rollup').read_text()
                        samples.append({k:int(v) for k,v in re.findall(r'^(\w+):\s+(\d+) kB$',contents,re.M)})
                    stages[line.strip().split('=')[1]]=samples
                    p.stdin.write('GO\n');p.stdin.flush()
            p.wait(timeout=5)
            text=''.join(lines)
            if p.returncode or 'INTEGRITY=PASS' not in text: raise RuntimeError('prototype failure')
            if any(s.get('Swap',0) for samples in stages.values() for s in samples): raise RuntimeError('swapped measurement')
            if set(stages)!={'idle','filled','both_copies','stored','verified'}: raise RuntimeError('missing stages')
            metrics={k:v for k,v in re.findall(r'^(\w+)=(.+)$',text,re.M) if k!='CHECKPOINT'}
            results.append({'pattern':pattern,'repeat':repeat+1,'checkpoints':stages,'metrics':metrics})
            (out/f'{repeat+1}-{pattern}.txt').write_text(text)
            (out/'measurements.json').write_text(json.dumps(results,indent=2)+'\n')
            print(f'{repeat+1}-{pattern} PASS',flush=True)
        finally:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
print('STORE_RUNS_PASS='+str(len(results)))
