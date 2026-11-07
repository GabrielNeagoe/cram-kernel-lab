"""Compare samples from fixed windows while the 10 GiB workload stays resident."""
import argparse,json,statistics
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args();base=a.directory
def lines(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
def read(p):return json.loads(p.read_text(encoding='utf-8'))
rows=[]
assert read(base/'low-startup.json')['verified_mib']==12288
for name in read(base/'measurement-mode.json').get('arms',('full1','control','full2')):
    path=base/('observer-'+name)/'samples.jsonl'
    host=lines(path if path.exists() else base/'observer/samples.jsonl')
    mp=base/('host-pages-'+name)/'windows-page-metadata.jsonl'
    metadata=lines(mp) if mp.exists() else []
    r=read(base/name/'result.json');events={e['event']:e for e in lines(base/name/'events.jsonl')}
    assert r['outcome']=='verified' and r['cleanup']['driver_unloaded']
    assert r['verified']['zswpin']-r['filled']['zswpin']==8192
    assert r['verified']['zswpwb']==r['filled']['zswpwb'] and r['verified']['oom_kills']==0
    release=events['control' if name=='control' else 'release']['time'];close=events['close']['time']
    last=r['samples'][-1]['time'];windows={}
    for label,lo,hi in [('baseline',release-20,release-5),('hold',close-25,close-5),('deflated',last-18,last-3)]:
        h=[s for s in host if lo<=s['guest_time']<=hi];assert len(h)>=4
        assert all(s['driver_loaded'] for s in h)
        if label!='hold' or name=='control':assert all(s['current']==0 and s['hold']=='Y' for s in h)
        else:assert all(s['hold']=='N' for s in h)
        data={}
        for key in ['vmx_ws','available','commit_free','current','target']+(['vmx_private_bytes'] if 'vmx_private_bytes' in h[0] else []):
            v=[s[key] for s in h];data[key]=dict(median=statistics.median(v),min=min(v),max=max(v))
        if metadata:
            scans=[s for s in metadata if h[0]['time']<=s['time'] and s['time']+s['seconds']<=h[-1]['time']]
            assert len(scans)>=2
            regions=[[reg for reg in s['regions'] if reg['kind']=='vmem'] for s in scans]
            assert all(sum(v['region_bytes'] for v in regs)==32*2**30 for regs in regions)
            v=[sum(reg['valid_pages']*4096 for reg in regs) for regs in regions]
            data['backing_bytes']=dict(median=statistics.median(v),min=min(v),max=max(v),samples=len(v))
        windows[label]=dict(host_samples=len(h),guest_time_window=[lo,hi],metrics=data)
    o=[s for s in r['samples'] if s['phase'] in ('open','control')];assert o
    assert max(s['current'] for s in r['samples'])*4096<=r['cap_bytes']
    matched=[s for s in o if s['target']>0 and s['current']==s['target']]
    longest=0;start=None
    for s in o:
        if 0<s['current']==s['target']:
            if start is None:start=s['time']
            longest=max(longest,s['time']-start)
        else:start=None
    assert len(matched)==r['exact_positive_samples'];d={}
    for key in ['available','vmx_ws','commit_free']+(['backing_bytes'] if metadata else [])+(['vmx_private_bytes'] if 'vmx_private_bytes' in windows['hold']['metrics'] else []):
        def val(w):return windows[w]['metrics'][key]['median']
        d[key+'_hold_minus_baseline']=val('hold')-val('baseline')
        d[key+'_deflated_minus_hold']=val('deflated')-val('hold')
    rows.append(dict(name=name,windows=windows,deltas_bytes=d,open_samples=len(o),exact_samples=len(matched),
        longest_consecutive_exact_seconds=longest,max_target_pages=max(s['target'] for s in o),
        max_current_pages=max(s['current'] for s in o),max_exact_pages=max((s['current'] for s in matched),default=0),
        acquired_original_pages=r['acquisition']['lab_acquired'],original_offline_at_first_match=r['original_offline_at_first_match'],
        original_offline_before_deflation=r['original_offline_before_deflation'],min_guest_available_kib=min(s['available_kib'] for s in r['samples'])))
summary=dict(cap_bytes=4*2**30,measurement_mode=read(base/'measurement-mode.json'),trials=rows,
    method='Host reads include the guest sample timestamp; fixed 15/20/15-second baseline, final hold and deflated windows exclude transitions. Guest ballast and compressed workload remain live through all three windows.')
(base/'comparison.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
for r in rows:print(r['name'],r['exact_samples'],r['open_samples'],r['max_exact_pages'],r['deltas_bytes'])
