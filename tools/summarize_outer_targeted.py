"""Validate exact-frame offers and compare their Windows working-set windows."""
import json
from pathlib import Path
from statistics import median
import sys

base=Path(sys.argv[1])
records=[json.loads(line) for line in (base/'windows-samples.jsonl').read_text().splitlines()]
metadata=[json.loads(line) for line in Path(sys.argv[2]).read_text().splitlines()] if len(sys.argv)>2 else []
result=json.loads((base/'guest-result.json').read_text())
assert result['outcome']=='completed' and len(result['trials'])==4
assert result['restored_driver']['current']==result['restored_driver']['target']==0
assert all(s['target']==s['current']==0 for s in result['native'])
assert any(r.get('event')=='finished' and r['outcome']=='completed' for r in records)
rows=[]
for i,mode in enumerate(('offer','hold','hold','offer'),1):
    trial=json.loads((base/f'{i:02d}-{mode}.json').read_text())
    acquired=trial['pool']['lab_acquired']
    expected=acquired if mode=='offer' else 0
    assert trial['after']['refused']==trial['before']['refused']==0
    assert trial['after']['not_needed']==trial['before']['not_needed']==0
    assert acquired>0 and acquired+trial['pool']['lab_failed']==8192
    assert all(s['target']==0 and s['current']==expected and not s['resetting'] for s in trial['samples'])
    trace=trial['frame_trace']
    assert trace['newly_offline']==trace['original_in_newly_offline']==expected and trace['baseline_restored']
    assert trial['verified']['zswpin']-trial['filled']['zswpin']==8192
    assert trial['verified']['memory.cram.reserved']==trial['verified']['oom_kills']==0
    assert trial['settled']['memory.cram.reserved']==2*2**20 and trial['settled']['swapped_pages']==8192
    assert trial['settled']['zswpwb']==trial['verified']['zswpwb']==0
    phases={}
    values={}
    for stage,window in (('baseline',5),('inflated',10),('deflated',5)):
        points=[r for r in records if r.get('kind')=='sample' and r['index']==i and r['stage']==stage]
        assert len(points)>=3
        fraction=sum(11*2**30<=p['available']<=13*2**30 for p in points)/len(points)
        span=points[-1]['t']-points[0]['t']
        if stage=='inflated': assert span>=29
        phases[stage]=dict(samples=len(points),fraction_on_target=fraction,span_seconds=span)
        values[stage]=median(p['vmx_ws'] for p in points if p['t']>=points[-1]['t']-window)
    rows.append(dict(trial=i,case=mode,acquired_pages=acquired,offered_original_pages=expected,
                     guest_charge_change_bytes=trial['settled']['memory.current']-trial['filled']['memory.current'],
                     windows_ws_change_bytes=values['inflated']-values['baseline'],
                     deflated_ws_change_bytes=values['deflated']-values['baseline'],
                     restored_entries=8192,readback='PASS',pressure_phases=phases,
                     pressure_valid=all(p['fraction_on_target']>=.9 for p in phases.values())))
    if metadata:
        backing={}; available={}; allocated={}; counts={}
        for stage,window in (('baseline',5),('inflated',10),('deflated',5)):
            points=[r for r in records if r.get('kind')=='sample' and r['index']==i and r['stage']==stage]
            end=points[-1]['wall_time']; begin=max(points[0]['wall_time'],end-window)
            snapshots=[m for m in metadata if 'error' not in m and begin<=m['time'] and m['time']+m['seconds']<=end]
            assert snapshots, (i,stage,'No complete backing-map scan inside window')
            pages=[]
            for m in snapshots:
                mapping=[v for v in m['regions'] if v['kind']=='vmem']
                assert len(mapping)==1 and mapping[0]['region_bytes']==32*2**30
                pages.append(mapping[0]['valid_pages'])
            backing[stage]=median(pages)*4096
            counts[stage]=len(pages)
            available[stage]=median(p['available'] for p in points if p['wall_time']>=begin)
            allocated[stage]=median(p['allocated'] for p in points if p['wall_time']>=begin)
        rows[-1].update(backing_resident_change_bytes=backing['inflated']-backing['baseline'],
                        backing_resident_after_deflation_change_bytes=backing['deflated']-backing['baseline'],
                        backing_scan_counts=counts,windows_available_change_bytes=available['inflated']-available['baseline'],
                        pressure_allocation_change_bytes=allocated['inflated']-allocated['baseline'],
                        acquisition_diagnostics=trial['pool'])
samples=[r for r in records if r.get('kind')=='sample']
assert all(not r['low'] and r['available']>=8*2**30 and r['commit_free']>=16*2**30 for r in samples)
recovery=[r for r in samples if r['stage']=='recovery']
assert recovery and all(r['allocated']==0 for r in recovery)
report=dict(trials=rows,standard_driver_restored=True,
            pressure_valid=all(r['pressure_valid'] for r in rows),
            peak_pressure_allocation_bytes=max(r['allocated'] for r in samples),
            windows_available_after_release=median(r['available'] for r in recovery[-10:]),
            taint_before=result['taint_before'],taint_after=result['taint_after'])
(base/'comparison.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2))
assert report['pressure_valid'], 'Retained result: pressure escaped the specified band'
