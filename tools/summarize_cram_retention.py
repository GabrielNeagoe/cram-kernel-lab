"""Compare reported re-entries in prespecified locked and unpinned file-page groups."""
import argparse,bisect,json
from pathlib import Path
from collections import Counter,defaultdict
from summarize_cram_frames import lines

def summarize(d,arm):
    folder=d/('host-pages-'+arm)
    groups=json.loads((folder/'pinned.json').read_text())
    locked=set(groups['pinned_indices'])
    previous=set(groups['historic_indices'])
    previous_result=json.loads((d.parent/'reentry-frames-01'/'reentry-comparison.json').read_text())
    evidence=json.loads((d/'rapid-comparison.json').read_text())[arm]
    assert all(evidence[k]['verified_by_bracketing_observer'] for k in ['physical_baseline','physical_hold'])
    lock=json.loads((folder/'locks-released.json').read_text())
    assert lock['locked_at_start_and_end']==4096
    assert len(locked)==4096 and len(previous)==2048 and len(locked & previous)==1024
    trial=json.loads((d/arm/'result.json').read_text())
    offered=set(trial.get('offline_indices_first') or []) & set(trial['offline_indices_final'])
    control=arm=='control'
    events={r['event']:r['time'] for r in lines(d/arm/'events.jsonl')}
    h=lines(d/('observer-'+arm)/'samples.jsonl')
    ht=[x['time'] for x in h]
    lo=evidence['physical_baseline']['end']
    hi=evidence['physical_hold']['start']
    windows={}
    def measure(label,r):
        w=windows.setdefault(label,{'counts':Counter(),'hit_indices':set(),'offered_hit_indices':set()})
        begin,end,count,lost,hits,within=r
        w['counts'].update(dict(reported_lost=lost,observed_records=count,tracked_records=len(hits),observed_file_records=within))
        w['hit_indices'].update(hits)
        w['offered_hit_indices'].update(i for i in hits if i in offered)
    for row in lines(folder/'reentry.jsonl'):
        a,b,*_=row
        if b>=lo and a<=hi:measure('transition',row)
        i=bisect.bisect_left(ht,a)-1;j=bisect.bisect_right(ht,b)
        if i<0 or j>=len(h) or ht[j]-ht[i]>6:continue
        if h[i]['guest_time']>=events['control' if control else 'exact_match'] and h[j]['guest_time']<=events['close']:
            if all(s['current']==0 and s['hold']=='Y' if control else s['current']==s['target']>0 and s['hold']=='N' for s in h[i:j+1]):measure('settled',row)
    for w in windows.values():
        indices=w.pop('hit_indices');confirmed=w.pop('offered_hit_indices')
        w['distinct_all']=len(indices);w['distinct_offered']=len(confirmed)
        w['distinct_locked_all']=len(indices & locked);w['distinct_unpinned_all']=len(indices-locked)
        w['previous_2048']=dict(locked_hits=len(indices & locked & previous),unpinned_hits=len((indices & previous)-locked),matched_entries=len(previous),locked_entries=len(previous & locked),confirmed_offered=len(offered & previous))
        c=w['counts'];w['reported_loss_fraction']=c['reported_lost']/max(c['reported_lost']+c['observed_records'],1)
    old=previous_result[arm]['windows']['between_exports_including_boundaries']
    guest_times=[s['guest_time'] for s in h]
    opening=events['control' if control else 'release']
    match=events['control' if control else 'exact_match']
    left=bisect.bisect_left(guest_times,opening)-1
    right=bisect.bisect_right(guest_times,match)
    assert 0<=left<right<len(h)
    return dict(windows=windows,prior_transition_lost=old['reported_lost'],prior_transition_loss_fraction=old['reported_lost']/max(1,old['reported_lost']+old['vmx_additions']),burst_interval_covers_inflation=lock['burst_start']<=ht[left] and lock['burst_end']>=ht[right],physical=evidence['physical_hold'],rapid_hold=evidence['hold'])
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);d=p.parse_args().directory
    out={arm:summarize(d,arm) for arm in ('full1','control')}
    (d/'retention-comparison.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))
