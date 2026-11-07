"""Compare real and simulated trim with identical live-buffer balloon tests."""
import argparse
import json
from pathlib import Path
import re
from statistics import median

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=Path)
args=p.parse_args()
summary={'trials':{},'limitation':'One matched pair; payload reclamation is distinct from RSS/PSS savings.'}
environments=[]
stages={'idle','filled','placed','verified','live_trimmed','live_restored','released','reclaimed','restored'}
for name,mode in [('simulated',1),('real',4)]:
    d=args.directory/name
    env=json.loads((d/'environment.json').read_text()); environments.append(env)
    assert env['balloon_test_trim_mode']==mode and env['exclusive'] and env['qemu_thp_disabled']
    rows=json.loads((d/'measurements.json').read_text()); assert len(rows)==1
    row=rows[0]; assert row['mode']=='cram' and row['placed_pages']==env['workload_bytes']//4096
    assert set(row['checkpoints'])==stages
    log=(d/(row['run']+'.txt')).read_text()
    assert re.findall(r'^LIVE_INTEGRITY=(\w+) PASS$',log,re.M)==['inflated','restored']
    assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',log)
    assert re.search(r'^BASELINE_EXIT=0$',log,re.M)
    transitions=[list(map(int,pair)) for pair in re.findall(r'^BALLOON_RATIO=(\d+) TRIM_COUNT=(\d+)$',log,re.M)]
    assert transitions==[[1500,32768],[3000,32768],[1000,76458],[3000,76458]],transitions
    measurements={}
    for stage,samples in row['checkpoints'].items():
        assert len(samples)==3
        assert all(s['rollup']['Swap']==0 and s['rollup']['AnonHugePages']==0 for s in samples)
        measurements[stage]={key:median(s['rollup'][key] for s in samples)/1024 for key in ('Rss','Pss')}
        measurements[stage]['payload_bytes']=samples[-1]['store']['store-payload-bytes']
    deltas={label:{key:measurements[end][key]-measurements[start][key] for key in ('Rss','Pss','payload_bytes')}
            for label,start,end in [('live_inflation','verified','live_trimmed'),
                                    ('released_inflation','released','reclaimed'),
                                    ('released_to_restored','released','restored')]}
    summary['trials'][name]={'stages':measurements,'deltas':deltas,'balloon_transitions':transitions,
                             'live_integrity':'PASS','placed_pages':row['placed_pages'],
                             'clock_warning':row['clock_warning']}
for key in ('kernel_sha256','qemu_sha256','workload_bytes','free_page_reporting','reporting_order',
            'pcp_high_fraction','malloc_trim_threshold','settle_seconds','reclaim_settle_seconds'):
    assert environments[0][key]==environments[1][key],key
summary['control_adjusted_reductions']={label:{key:summary['trials']['simulated']['deltas'][label][key]-summary['trials']['real']['deltas'][label][key]
      for key in ('Rss','Pss','payload_bytes')} for label in summary['trials']['real']['deltas']}
summary['units']='RSS/PSS in MiB; payload in bytes; positive adjusted reduction favors real trim'
(args.directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
