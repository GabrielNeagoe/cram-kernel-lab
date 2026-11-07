"""Validate five access-state runs and export their measured stages."""
import argparse
import csv
import json
from pathlib import Path
import re
import statistics

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('input',type=Path);p.add_argument('--prefix',required=True)
p.add_argument('--controls',action='store_true')
a=p.parse_args()
if not re.fullmatch(r'[a-z0-9-]+',a.prefix):p.error('Use a simple filename prefix')
out=Path(__file__).resolve().parents[1]/'evidence'
text=a.input.read_text(encoding='utf-8-sig')
text=re.sub(r'\([^()\s]+@[^)]+\)','(builder@lab)',text)
(out/(a.prefix+'.txt')).write_text(text,encoding='utf-8')
clean=re.sub(r'\[\s*\d+\.\d+\].*','',text)
assert re.findall(r'^PROBE_EXIT=(\d+)$',clean,re.M)==['0']*5
assert len(re.findall(r'^INTEGRITY,access-\d,PASS$',clean,re.M))==5
assert len(re.findall(r'^DEMOTION,access-\d,first=8192,cpu=0$',clean,re.M))==5
assert len(re.findall(r'^PLACEMENT,access-\d,1,cxl=8192,other=0,absent=0,zero_pfn=0$',clean,re.M))==5
assert 'QEMU_EXIT=0' in clean
rows=[]
for kind,run,stage,cpu,ms,minor,major in re.findall(r'^(READ|FLUSH),([^,]+),([^,]+),cpu=(\d+),ms=([\d.]+),minor=(\d+),major=(\d+)$',clean,re.M):
    rows.append(dict(kind=kind,run=run,stage=stage,cpu=int(cpu),elapsed_ms=float(ms),minor=int(minor),major=int(major)))
assert len(rows)==(95 if a.controls else 55)
assert len({(r['run'],r['stage']) for r in rows})==len(rows)
assert all(r['cpu']==0 and r['major']==0 and (r['minor']==0 or r['stage']=='dram_warm') for r in rows)
summary=dict(runs=5,integrity_passes=5,fully_demoted_pages_per_run=8192,stages={})
for stage in dict.fromkeys(r['stage'] for r in rows):
    vals=[r['elapsed_ms'] for r in rows if r['stage']==stage];assert len(vals)==5
    summary['stages'][stage]=dict(n=5,median_ms=statistics.median(vals),min_ms=min(vals),max_ms=max(vals))
with (out/(a.prefix+'.csv')).open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(out/(a.prefix+'-summary.json')).write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
