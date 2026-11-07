"""Compare confirmed VMware balloon size and Windows working set by workload."""
import argparse
import json
from pathlib import Path
from statistics import median

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
parser.add_argument('windows_samples',type=Path)
parser.add_argument('--controlled-pressure',action='store_true')
args=parser.parse_args()
base=args.directory
read=lambda name:json.loads((base/name).read_text(encoding='utf-8-sig'))
result=read('guest-result.json')
assert result['outcome']=='completed'
assert result['restored_driver']['current']==result['restored_driver']['target']==0
assert all(s['current']==s['target']==0 for s in result['native'])
assert result['native'][-1]['target_queries'][0]>result['native'][0]['target_queries'][0]
records=[json.loads(l) for l in args.windows_samples.read_text().splitlines() if l.strip()]
assert any(r.get('event')=='finished' for r in records)
rows=[]
for i,peak in enumerate(result['trials'],1):
    case=('arena','resident','resident','arena')[i-1]
    trial=read(f'{i:02d}-{case}.json')
    observed=trial['samples']
    assert peak==max(s['current'] for s in observed)
    assert trial['after']['current']==0 and all(s['target']==0 for s in observed)
    assert trial['verified']['oom_kills']==0 and trial['verified']['memory.cram.reserved']==0
    if case=='arena':
        assert trial['settled']['memory.cram.reserved']>0 and trial['verified']['zswpin']>trial['filled']['zswpin']
    else:
        assert trial['settled']['present_pages']==8192
    def window(stage,seconds):
        samples=[r for r in records if r.get('kind')=='sample' and r['index']==i and r['stage']==stage]
        if len(samples)<3:
            raise ValueError(f'Insufficient Windows samples for trial {i} stage {stage}')
        end=samples[-1]['t']
        return [r['vmx_ws'] for r in samples if r['t']>=end-seconds]
    baseline=window('baseline',5)
    stage='inflated' if peak==8192 else 'offer'
    comparison=window(stage,10)
    rows.append(dict(trial=i,case=case,requested_mib=32,accepted_peak_pages=peak,accepted_mib=peak/256,
                     host_target_pages=0,not_needed_responses=trial['after']['not_needed']-trial['before']['not_needed'],
                     guest_charge_change=trial['settled']['memory.current']-trial['filled']['memory.current'],
                     entries_restored=trial['verified']['zswpin']-trial['filled']['zswpin'],
                     readback='PASS',sampling_phase=stage,windows_ws_change_bytes=median(comparison)-median(baseline),
                     baseline_count=len(baseline),comparison_count=len(comparison)))
    if 'frame_trace' in trial:
        trace=trial['frame_trace']
        assert trace['original_pages']==8192 and trace['newly_offline']==peak
        assert trace['returned_online']==peak and trace['baseline_restored']
        assert 0<=trace['original_in_newly_offline']<=peak
        if case=='resident':
            assert trace['original_in_newly_offline']==0
        rows[-1]['frame_trace']=trace
    if args.controlled_pressure:
        # Apply the earlier 11–13 GiB band separately to each measured phase.
        phases={}
        for phase in ('baseline','inflated','deflated'):
            points=[r for r in records if r.get('kind')=='sample' and r['index']==i and r['stage']==phase]
            assert points
            fraction=sum(11*2**30<=p['available']<=13*2**30 for p in points)/len(points)
            phases[phase]=dict(samples=len(points),fraction_on_target=fraction,
                               median_available_bytes=median(p['available'] for p in points),
                               span_seconds=round(points[-1]['t']-points[0]['t'],3))
        assert all(v['fraction_on_target']>=.9 for v in phases.values())
        assert phases['inflated']['span_seconds']>=29 and peak==8192
        first=next(j for j,s in enumerate(observed) if s['current']==peak)
        assert all(s['current']==peak for s in observed[first:])
        rows[-1]['pressure_phases']=phases
        rows[-1]['deflated_ws_change_bytes']=median(window('deflated',5))-median(baseline)
report={'native_target_pages':0,'native_current_pages':0,'trials':rows,
        'restored_driver':True,'taint_before':result['taint_before'],'taint_after':result['taint_after']}
if args.controlled_pressure:
    samples=[r for r in records if r.get('kind')=='sample']
    assert all(not r['low'] and r['available']>=8*2**30 and r['commit_free']>=16*2**30 for r in samples)
    recovery=[r for r in samples if r['stage']=='recovery']
    assert recovery and all(r['allocated']==0 for r in recovery)
    report.update(peak_pressure_allocation_bytes=max(r['allocated'] for r in samples),
                  windows_available_after_release=median(r['available'] for r in recovery[-10:]))
(base/'comparison.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2))
