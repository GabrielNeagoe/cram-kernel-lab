"""Count nonresident observations and report the measured timing gaps and snapshot windows."""
import bisect
import json
import statistics
from pathlib import Path
from summarize_cram_frames import lines

def summarize(base, arm):
    src=base/('host-pages-'+arm)
    host=lines(base/('observer-'+arm)/'samples.jsonl')
    events={x['event']:x['time'] for x in lines(base/arm/'events.jsonl')}
    times=[x['time'] for x in host]
    scans=lines(src/'rapid.jsonl')
    assert scans and all(t1>=t0 and (not bits if n==0 else len(bits)==2048 and int(bits,16).bit_count()==n) for t0,t1,n,bits in scans)
    control=arm=='control'
    opening=events['control' if control else 'release']
    exact=events['control' if control else 'exact_match']
    groups=dict(all=scans,baseline=[],ramp=[],hold=[],deflated=[])
    for scan in scans:
        t0,t1=scan[:2]
        i=bisect.bisect_left(times,t0/1e9)-1
        j=bisect.bisect_right(times,t1/1e9)
        if i<0 or j>=len(times):continue
        left,right=host[i],host[j]
        if right['time']-left['time']>6:continue
        lo,hi=left['guest_time'],right['guest_time']
        if lo>=events['baseline'] and hi<opening and left['current']==right['current']==0:groups['baseline'].append(scan)
        if not control and lo<=exact+2 and hi>=opening-2:groups['ramp'].append(scan)
        held=all(x['current']==0 and x['hold']=='Y' if control else x['current']==x['target']>0 and x['hold']=='N' for x in [left,right])
        if lo>=exact and hi<events['close'] and held:groups['hold'].append(scan)
        if lo>=events['deflated'] and hi<events['driver_unloaded']:groups['deflated'].append(scan)
    result={}
    for group,rows in groups.items():
        if not rows:continue
        gaps=[(b[1]-a[0])/1e6 for a,b in zip(rows,rows[1:])]
        mask=0
        for _,_,n,bits in rows:
            if n:mask|=int(bits,16)
        result[group]=dict(scans=len(rows),observed_nonresident_pages=mask.bit_count(),
            scans_with_nonresident=sum(r[2]>0 for r in rows),median_scan_ms=statistics.median((r[1]-r[0])/1e6 for r in rows),
            median_interval_ms=statistics.median((b[0]-a[0])/1e6 for a,b in zip(rows,rows[1:])),max_start_to_next_end_ms=max(gaps))
    assert all(result[k]['scans']>=100 for k in ('baseline','hold','deflated'))
    for phase in ('baseline','hold'):
        path=src/('physical-'+phase+'.json')
        if not path.exists():
            result['physical_'+phase]=dict(usable=False,reason=json.loads((src/('physical-'+phase+'-error.json')).read_text()) if (src/('physical-'+phase+'-error.json')).exists() else 'missing')
            continue
        snap=json.loads(path.read_text())
        i=bisect.bisect_left(times,snap['start'])-1
        j=bisect.bisect_right(times,snap['end'])
        segment=host[i:j+1]
        lo,hi=(events['baseline'],opening) if phase=='baseline' else (exact,events['close'])
        inphase=bool(i>=0 and j<len(times) and segment and lo<=segment[0]['guest_time'] and segment[-1]['guest_time']<=hi)
        if phase=='hold' and not control:
            inphase=inphase and all(x['current']==x['target']>0 and x['hold']=='N' for x in segment)
        else:
            inphase=inphase and all(x['current']==0 and x['hold']=='Y' for x in segment)
        result['physical_'+phase]=dict(**snap,verified_by_bracketing_observer=inphase)
    return result
if __name__=='__main__':
    import sys
    base=Path(sys.argv[1])
    r={arm:summarize(base,arm) for arm in ['full1','control']}
    (base/'rapid-comparison.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))
