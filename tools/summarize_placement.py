"""Validate placement/CPU probe evidence and write public CSV and JSON."""
import csv
import json
from pathlib import Path
import re
import statistics
root=Path(__file__).resolve().parents[1]
text=(root/'private/placement-study-raw.txt').read_text(encoding='utf-8-sig')
text=re.sub(r'\([^()\s]+@[^)]+\)','(builder@lab)',text)
(root/'evidence/placement-study.txt').write_text(text,encoding='utf-8')
clean=re.sub(r'\[\s*\d+\.\d+\].*','',text)
passes=re.findall(r'^INTEGRITY,([^,]+),PASS$',clean,re.M)
exits=re.findall(r'^PROBE_EXIT=(\d+)$',clean,re.M)
demotions=re.findall(r'^DEMOTION,([^,]+),first=(\d+),cpu=(\d+)$',clean,re.M)
assert len(passes)==len(exits)==len(demotions)==16 and set(exits)=={'0'}
assert all(int(x[1])==8192 for x in demotions)
placements=[]
for run,stage,cxl,other,absent,zero in re.findall(r'^PLACEMENT,([^,]+),(\d+),cxl=(\d+),other=(\d+),absent=(\d+),zero_pfn=(\d+)$',clean,re.M):
    placements.append(dict(run=run,stage=int(stage),cxl=int(cxl),other=int(other),absent=int(absent),zero_pfn=int(zero)))
assert len(placements)==20
assert all(row['cxl']==8192 and row['other']==row['absent']==row['zero_pfn']==0 for row in placements if row['stage']==1)
reads=[]
for run,stage,cpu,ms,minor,major in re.findall(r'^READ,([^,]+),([^,]+),cpu=(\d+),ms=([\d.]+),minor=(\d+),major=(\d+)$',clean,re.M):
    reads.append(dict(run=run,stage=stage,cpu=int(cpu),elapsed_ms=float(ms),minor=int(minor),major=int(major)))
assert len(reads)==36
for name,data in [('placement-observations.csv',placements),('cpu-read-observations.csv',reads)]:
    with (root/'evidence'/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
summary=dict(integrity_passes=16,placement_runs=10,cpu_runs=6,partial_demotions=0,read_stages={})
for stage in dict.fromkeys(row['stage'] for row in reads):
    values=[row['elapsed_ms'] for row in reads if row['stage']==stage]
    summary['read_stages'][stage]=dict(n=len(values),median_ms=statistics.median(values),min_ms=min(values),max_ms=max(values))
(root/'evidence/placement-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
