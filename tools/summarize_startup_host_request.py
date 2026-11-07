"""Summarize measured host-request trials; never infer unique physical release."""
import json
import statistics
from pathlib import Path

base = Path('evidence/arena-reservation-20261103/vm-candidate/host-requested-balloon/startup-guard')
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def lines(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()]

pages = lines(base/'low1/host-pages/windows-page-metadata.jsonl')
host = lines(base/'low1/observer/samples.jsonl')
assert not any('error' in r for r in pages)
assert read(base/'low1/startup.json')['host_limit_verified_mib'] == 12288
segments = []
active = None
for sample in host:
    if sample['driver_loaded']:
        if active is None:
            active = []
            segments.append(active)
        active.append(sample)
    else:
        active = None
assert len(segments) == 3
rows = []
for name, segment in zip(('low1', 'control1', 'low2'), segments):
    result = read(base/name/'result.json')
    events = {r['event']: r for r in lines(base/name/'events.jsonl')}
    control = name == 'control1'
    # All comparison windows use the Windows clock, including metadata scan ends.
    # Guest timestamps are retained as evidence, not mixed into host phase bounds.
    end = segment[-1]['time']
    release = end-60 if control else next(r['time'] for r in segment if r['current'] > 0)
    acquired = result['acquisition']['lab_acquired']
    assert result['outcome'] == 'verified' and result['cleanup']['driver_unloaded']
    assert result['verified']['zswpin']-result['filled']['zswpin'] == 8192
    assert result['verified']['memory.cram.reserved'] == result['verified']['oom_kills'] == 0
    assert result['verified']['zswpwb']-result['filled']['zswpwb'] == 0
    assert all(0 <= r['current'] <= acquired for r in result['samples'])
    assert result['offered_original_frames'] == (0 if control else acquired)
    assert control or result['host_requested_inflation']
    windows = {}
    for label, start, stop in [('baseline', release-15, release-5), ('hold', end-25, end-5)]:
        p = [r for r in pages if start <= r['time'] and r['time']+r['seconds'] <= stop]
        h = [r for r in host if start <= r['time'] <= stop]
        assert len(p) >= 3 and len(h) >= 3
        assert all(r['driver_loaded'] for r in h)
        assert any(r['target'] > 0 for r in h)
        if label == 'hold' and not control:
            assert all(r['target'] > 0 for r in h)
        assert all(r['current'] == (0 if control or label == 'baseline' else acquired) for r in h)
        backing = [sum(v['valid_pages']*4096 for v in r['regions'] if v['kind']=='vmem') for r in p]
        windows[label] = dict(start=start, end=stop, metadata_samples=len(p), host_samples=len(h),
            backing_bytes_median=statistics.median(backing), backing_bytes_min=min(backing),
            backing_bytes_max=max(backing), vmx_ws_median=statistics.median(r['vmx_ws'] for r in h),
            windows_available_median=statistics.median(r['available'] for r in h),
            host_target_min_pages=min(r['target'] for r in h),
            host_target_max_pages=max(r['target'] for r in h))
    a, z = windows['baseline'], windows['hold']
    rows.append(dict(run=name, control=control, acquired_pages=acquired,
        offered_bytes=result['offered_original_frames']*4096, windows=windows,
        backing_bytes_delta=z['backing_bytes_median']-a['backing_bytes_median'],
        vmx_ws_delta=z['vmx_ws_median']-a['vmx_ws_median'],
        windows_available_delta=z['windows_available_median']-a['windows_available_median']))
summary = dict(host_limit_mib=12288, min_vm_mem_pct=25, real_host_requested_inflation=True,
    window_method='Windows-clock-only intervals with 5-second boundary guards. Offer baseline precedes first observed inflation. Control baseline precedes the final 60-second hold, estimated backward from the last loaded-driver sample. Metadata scans must finish inside their window.',
    trials=rows, limitations='Mapping residency and system-wide available memory do not identify unique physical pages; finite sequential trials are not a causal proof of Windows RAM return.')
(base/'comparison.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary, indent=2))
