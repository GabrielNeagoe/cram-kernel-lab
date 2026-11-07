"""Summarize a post-balloon explicit malloc_trim diagnostic; no matched claim."""
import argparse
import json
from pathlib import Path
import re
from statistics import median

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=Path)
args=p.parse_args()
env=json.loads((args.directory/'environment.json').read_text())
assert env['balloon_test_trim_mode']==4 and env['trim_on_release'] and env['trim_stage']=='reclaimed'
rows=json.loads((args.directory/'measurements.json').read_text());assert len(rows)==1
row=rows[0]; log=(args.directory/(row['run']+'.txt')).read_text()
assert re.findall(r'^LIVE_INTEGRITY=(\w+) PASS$',log,re.M)==['inflated','restored']
assert row['placed_pages']==env['workload_bytes']//4096
assert re.search(r'^BASELINE_EXIT=0$',log,re.M)
assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',log)
for samples in row['checkpoints'].values():
    assert all(s['rollup']['Swap']==0 and s['rollup']['AnonHugePages']==0 for s in samples)
before=row['checkpoints']['reclaimed']
after=before[0]['after_trim']
assert all(s['rollup']['Swap']==0 and s['rollup']['AnonHugePages']==0 for s in after)
def physical(samples):
    return {k:median(s['rollup'][k] for s in samples)/1024 for k in ('Rss','Pss')}
result={'before_trim_mib':physical(before),'after_trim_mib':physical(after),
        'restored_mib':physical(row['checkpoints']['restored']),
        'allocator_before':before[0]['allocator'],
        'allocator_after':before[0]['allocator_after_trim'],
        'payload_bytes_before_trim':before[0]['store']['store-payload-bytes'],
        'payload_bytes_restored':row['checkpoints']['restored'][0]['store']['store-payload-bytes'],
        'live_integrity':'PASS','clock_warning':row['clock_warning'],
        'limitation':'Unpaired allocator diagnostic; reclaimed resident memory includes general QEMU heap, not solely CRAM payload.'}
result['returned_mib']={k:result['before_trim_mib'][k]-result['after_trim_mib'][k] for k in ('Rss','Pss')}
(args.directory/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
