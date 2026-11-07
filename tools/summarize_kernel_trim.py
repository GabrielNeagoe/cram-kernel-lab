"""Validate positive and rejected kernel-trim guest evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
args=parser.parse_args()
results={}
for name in ('positive','rejected'):
    text=(args.directory/(name+'.txt')).read_text()
    assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',text)
    assert 'HELPER_TESTS_PASS=9' in text and 'LAB_TRIM_CYCLES_PASS=2' in text
    assert re.findall(r'^TEST_EXIT=(\d+)$',text,re.M)==['0']*9
    counts=[int(x) for x in re.findall(r'^LAB_TRIM_COUNT=(\d+)$',text,re.M)]
    assert counts==([218,436] if name=='positive' else [0,0]),counts
    states={}
    for stage in ('INFLATED','DEFLATED'):
        rows=re.findall(r'^LAB_TRIM_'+stage+r'\nnode[^\n]*\n(2 [0-9 ]+)$',text,re.M)
        assert len(rows)==2
        states[stage.lower()]=[[int(x) for x in row.split()] for row in rows]
        assert all(row[4]==row[5]==(218 if stage=='INFLATED' else 0)
                   for row in states[stage.lower()])
    recovery=json.loads((args.directory/(name+'.json')).read_text())
    assert recovery['promotion_failures']==0 and recovery['qemu_exit']==0
    assert recovery['recovery'][-1]['free']==65536
    results[name]={'trim_counts':counts,'balloon_cycles':2,'helper_tests':9,
                   'states':states,'recovery':recovery['recovery'],
                   'promotion_failures':0,'qemu_exit':0,
                   'clock_warning':'clocksource:' in text}
files=['experiments/cxl-cram-lab-trim.inc','tools/patch_kernel_lab_trim.py',
       'tools/run_placement_study.py','tools/summarize_kernel_trim.py',
       'patches/kernel-cram-lab-trim.patch']
results['source_sha256']={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files}
results['scope']='Nested emulator only; no matched RSS/PSS or live-neighbor-during-trim claim'
(args.directory/'validation.json').write_text(json.dumps(results,indent=2)+'\n')
print('PASS: 18 guest helpers, four balloon cycles, success and rejection accounting, full recovery')
