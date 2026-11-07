"""Validate residual page observations and the separate pinned control run."""
import csv
import json
from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]/'evidence'
capture=root/'phases/cram-phases-20261024T161400Z-efe67733'
text=(capture/'serial.txt').read_text()
residuals=[]
for run,stage,index,pfn,flags,count,cpu in re.findall(r'^RESIDUAL,([^,]+),pass=(\d+),index=(\d+),pfn=(0x[\da-f]+),flags=(0x[\da-f]+),mapcount=(\d+),cpu=(\d+)$',text,re.M):
    residuals.append(dict(run=run,stage=int(stage),page_index=int(index),pfn=pfn,flags=flags,mapcount=int(count),cpu=int(cpu),lru=bool(int(flags,16)&(1<<5))))
assert residuals and all(not r['lru'] and r['mapcount']==1 for r in residuals)
states=[]
for run,stage,cxl,other,absent,zero in re.findall(r'^PAGESTATE,([^,]+),pass=(\d+),cxl=(\d+),other=(\d+),absent=(\d+),zero=(\d+)$',text,re.M):
    states.append(dict(run=run,stage=int(stage),cxl=int(cxl),other=int(other),absent=int(absent),zero=int(zero)))
assert len(states)==9
assert all(r['cxl']==8192 and r['other']==0 for r in states if r['stage']==2)
assert all(r['absent']==r['zero']==0 for r in states)
for name,data in [('residual-pages.csv',residuals),('partial-retry-observations.csv',states)]:
    with (root/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
pinned=json.loads((root/'phases/cram-phases-20261024T161502Z-23246f75/manifest.json').read_text())
assert pinned['validation']=='PASS' and pinned['guest_exits']==[0]*6 and len(pinned['phases'])==39
summary=dict(initial_residual_pages={r['run']:r['other'] for r in states if r['stage']==0},
             all_residuals_singly_mapped_without_lru=True,cpu0_retry_moved=0,cpu1_retry_moved=31,
             pinned_runs_passed=6,states=states)
assert re.findall(r'^RETRY_PARTIAL,[^,]+,pass=1,demoted=(\d+)$',text,re.M)==['0']*3
assert re.findall(r'^RETRY_PARTIAL,[^,]+,pass=2,demoted=(\d+)$',text,re.M)==['7','11','13']
(root/'partial-retry-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
