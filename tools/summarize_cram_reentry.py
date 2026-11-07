"""Summarize monitored additions without assigning boundary-crossing drains to a hold."""
from pathlib import Path
import argparse
import bisect
import json
from collections import Counter
from summarize_cram_frames import lines

def summarize(base,arm):
    trials=json.loads((base/arm/'result.json').read_text())
    assert trials['outcome']=='verified'
    offered=set(trials.get('offline_indices_first') or []) & set(trials['offline_indices_final'])
    events={e['event']:e['time'] for e in lines(base/arm/'events.jsonl')}
    host=lines(base/('observer-'+arm)/'samples.jsonl')
    host_times=[r['time'] for r in host]
    control=arm=='control'
    opening=events['control' if control else 'release']
    full=events['control' if control else 'exact_match']
    groups={}
    distinct={}
    clean={}
    folder=base/('host-pages-'+arm)
    before=json.loads((folder/'physical-baseline.json').read_text())
    after=json.loads((folder/'physical-hold.json').read_text())
    def add(label,row):
        beg,end,count,lost,hits,file_count=row
        r=groups.setdefault(label,Counter())
        r['drains']+=1;r['seconds']+=end-beg
        r['vmx_additions']+=count;r['vmem_additions']+=file_count
        distinct.setdefault(label,set()).update(hits)
        r['tracked_additions']+=len(hits)
        r['offered_tracked_additions']+=sum(i in offered for i in hits)
        r['reported_lost']+=lost;r['drains_with_loss']+=int(lost!=0)
        if lost: r['seconds_in_drains_reporting_loss']+=end-beg
        last,span=clean.get(label,(None,0))
        span=(span if last==beg else 0)+(end-beg) if not lost else 0
        clean[label]=(end,span)
        r['longest_contiguous_seconds_without_reported_loss']=max(span,r['longest_contiguous_seconds_without_reported_loss'])
    transition_hits=[]
    prev=None
    for row in lines(base/('host-pages-'+arm)/'reentry.jsonl'):
        begin,end,count,lost,hits,file_count=row
        assert end>=begin and (prev is None or begin==prev)
        assert 0<=len(hits)<=file_count<=count and all(0<=idx<8192 for idx in hits)
        prev=end
        add('all',row)
        # These acquisition bounds include the inflation transition. Replaying
        # inclusive boundary drains here avoids dropping events at either edge.
        if end>=before['start'] and begin<=after['end']:add('baseline_start_through_hold_export_end',row)
        if end>=before['end'] and begin<=after['start']:
            add('between_exports_including_boundaries',row)
            if hits:
                i=bisect.bisect_left(host_times,begin)-1
                j=bisect.bisect_right(host_times,end)
                assert i>=0 and j<len(host)
                transition_hits.append(dict(
                    drain_duration_ms=(end-begin)*1000,read_end=end,target_records=len(hits),
                    dropped=lost,observer_before_current=host[i]['current'],observer_after_current=host[j]['current'],
                    observer_before_gate=host[i]['hold'],observer_after_gate=host[j]['hold'],
                    observer_before_guest_s_from_opening=host[i]['guest_time']-opening,
                    observer_after_guest_s_from_opening=host[j]['guest_time']-opening))
        i=bisect.bisect_left(host_times,begin)-1;j=bisect.bisect_right(host_times,end)
        if i<0 or j>=len(host) or host_times[j]-host_times[i]>6:continue
        h=host[i:j+1]
        lo,hi=h[0]['guest_time'],h[-1]['guest_time']
        if lo>=events['baseline'] and hi<=opening and all(s['current']==0 for s in h):add('baseline',row)
        if lo>=full and hi<=events['close'] and all(s['current']==0 and s['hold']=='Y' if control else s['current']==s['target']>0 and s['hold']=='N' for s in h):add('hold',row)
        if lo>=events['deflated'] and hi<=events['driver_unloaded'] and all(s['current']==0 for s in h):add('deflated',row)
    assert all(groups[k]['seconds']>10 for k in ('baseline','hold','deflated'))
    for k,values in distinct.items():groups[k]['unique_tracked_indices']=len(values)
    return dict(verified_offsets=8192,between_export_hit_drains=transition_hits,confirmed_offered_at_both_checks=len(offered),windows=groups)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('base',type=Path)
    base=p.parse_args().base
    result={arm:summarize(base,arm) for arm in ('full1','control')}
    (base/'reentry-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v['windows']['hold'] for k,v in result.items()},indent=2))
