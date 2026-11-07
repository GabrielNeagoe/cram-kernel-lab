"""Compare accepted matched host-pressure trials and retain out-of-band trials."""
import argparse
import json
from pathlib import Path
from statistics import median

GIB = 2**30

def summarize(path, base):
    trial = json.loads(path.read_text())
    samples = trial['samples']
    filled = [s for s in samples if s['stage'] == 'filled']
    hold = [s for s in samples if s['stage'] == 'hold']
    last = [s for s in hold if s['t'] >= hold[-1]['t'] - 20]
    recovery = [s for s in samples if s['stage'] == 'recovery']
    guest = trial['guest']
    fraction = sum(11*GIB <= s['available'] <= 13*GIB for s in hold) / len(hold)
    ws_start = median(s['vmx_ws'] for s in filled)
    assert hold[-1]['t'] - hold[0]['t'] >= 118
    assert all(not s['low'] and s['available'] >= 8*GIB and s['commit_free'] >= 16*GIB for s in samples)
    assert recovery and all(s['helper_ws'] == 0 and s['allocated'] == 0 for s in recovery)
    assert guest['verified']['memory.cram.reserved'] == 0 and guest['verified']['oom_kills'] == 0
    if trial['case'] == 'arena':
        assert guest['settled']['swapped_pages'] == 8192
        assert guest['verified']['zswpin'] > guest['settled']['zswpin']
    else:
        assert guest['settled']['present_pages'] == 8192 and guest['settled']['swapped_pages'] == 0
    assert bool(trial.get('verified')) == (fraction >= 0.9)
    return dict(source=path.relative_to(base).as_posix(), case=trial['case'], index=trial['trial'],
                accepted=fraction>=0.9, fraction_on_target=fraction,
                hold_span_seconds=round(hold[-1]['t']-hold[0]['t'],3),
                host_available_at_start=median(s['available'] for s in filled),
                host_available_under_pressure=median(s['available'] for s in hold),
                pressure_peak_allocation=max(s['allocated'] for s in hold),
                pressure_process_resident=median(s['helper_ws'] for s in hold),
                vmx_ws_before=ws_start, vmx_ws_late_change=median(s['vmx_ws'] for s in last)-ws_start,
                vmx_ws_minimum_change=min(s['vmx_ws'] for s in hold)-ws_start,
                guest_charge_change=guest['settled']['memory.current']-guest['filled']['memory.current'],
                restored_pages=guest['verified']['zswpin']-guest['settled']['zswpin'],
                digest_verified=True, temporary_reservations_at_exit=0,
                balloon_inflate_delta=trial['vmstat_after']['balloon_inflate']-trial['vmstat_before']['balloon_inflate'],
                balloon_deflate_delta=trial['vmstat_after']['balloon_deflate']-trial['vmstat_before']['balloon_deflate'],
                host_available_after_release=median(s['available'] for s in recovery[-10:]))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    base=parser.parse_args().directory
    rows=[summarize(p,base) for p in sorted(base.rglob('[0-9][0-9]-*.json'))]
    valid=sorted((r for r in rows if r['accepted']),key=lambda r:r['index'])
    assert [(r['index'],r['case']) for r in valid]==[(1,'resident'),(2,'arena'),(3,'arena'),(4,'resident')]
    pairs=[valid[1]['vmx_ws_late_change']-valid[0]['vmx_ws_late_change'],
           valid[2]['vmx_ws_late_change']-valid[3]['vmx_ws_late_change']]
    result=dict(target_available_bytes=12*GIB,allowed_band_bytes=GIB,required_fraction=.9,
                hold_seconds=120,guest_buffer_bytes=32*2**20,matched=valid,
                unmatched=[r for r in rows if not r['accepted']],arena_minus_control_ws_change_bytes=pairs)
    (base/'comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
