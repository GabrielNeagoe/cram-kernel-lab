"""Summarize within-page residency changes only during independently observed phases."""
import argparse
import bisect
import json
from pathlib import Path

def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def analyze(base, arm):
    trial=json.loads((base/arm/'result.json').read_text())
    assert trial['outcome']=='verified' and trial['verified']['zswpin']-trial['filled']['zswpin']==8192
    events={e['event']:e['time'] for e in lines(base/arm/'events.jsonl')}
    count=trial.get("marker_count",256)
    assert count in (256,8192)
    control=arm=='control'
    if control:
        assert trial['offline_indices_final']==[] and trial['exact_positive_samples']==0
        cohort=set(range(count))
    else:
        cohort=set(trial['offline_indices_first']) & set(trial['offline_indices_final'])
        assert cohort and trial['exact_positive_samples']>0
    phases={
        'baseline': (events['baseline']+2, events['control' if control else 'release']-2),
        'hold': (events['control' if control else 'exact_match']+2,events['close']-2),
        'deflated': (events['deflated']+2,events['driver_unloaded']-2)}
    host=lines(base/('observer-'+arm)/'samples.jsonl')
    times=[s['time'] for s in host]
    assert times==sorted(times)
    scans=lines(base/('host-pages-'+arm)/'windows-page-metadata.jsonl')
    data={k:[] for k in phases}
    all_resident_mask=(1<<count)-1
    eligible_mask=sum(1<<i for i in cohort)
    for scan in scans:
        if 'resident_bits' not in scan: continue
        t=scan['frame_sample_time']
        i=bisect.bisect_right(times,t)
        if not 0<i<len(times): continue
        left,right=host[i-1:i+1]
        if right['time']-left['time']>6: continue
        for phase,(lo,hi) in phases.items():
            if not (lo<=left['guest_time']<=right['guest_time']<=hi): continue
            if not all(s['driver_loaded'] for s in (left,right)): continue
            if phase=='hold' and not control:
                if not all(s['current']==s['target']>0 and s['hold']=='N' for s in (left,right)): continue
            elif not all(s['current']==0 and s['hold']=='Y' for s in (left,right)): continue
            bits=bytes.fromhex(scan['resident_bits'])
            assert len(bits)==count//8
            data[phase].append(int.from_bytes(bits,'little'))
    assert all(len(v)>=5 for v in data.values()), {k:len(v) for k,v in data.items()}
    from functools import reduce
    from operator import and_, or_
    baseline=reduce(and_,data['baseline'])
    always=reduce(and_,data['hold'])
    ever=reduce(or_,data['hold'])
    def total(mask): return (mask & eligible_mask).bit_count()
    return dict(marker_verified_pages=count,eligible_pages=len(cohort),baseline_always_resident=total(baseline),
                all_original_resident_every_hold_scan=(always & all_resident_mask).bit_count(),
                baseline_resident_ever_nonresident_in_hold=total(baseline & ~always),
                baseline_resident_nonresident_throughout_hold=total(baseline & ~ever),
                always_resident_through_deflated_samples=total(baseline & always & reduce(and_,data['deflated'])),
                phases={k:dict(samples=len(v),min_resident=min(map(total,v)),max_resident=max(map(total,v))) for k,v in data.items()})


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('directory',type=Path)
    base=p.parse_args().directory
    result={arm:analyze(base,arm) for arm in ['full1','control']}
    (base/'frame-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
