"""Summarize repeated integrity checks and mapping growth after release."""
import argparse
import json
from pathlib import Path
from statistics import median

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory', type=Path)
args = p.parse_args()
env = json.loads((args.directory/'environment.json').read_text())
rows = json.loads((args.directory/'measurements.json').read_text())
assert env['detailed_maps'] and env['exclusive']
cycles = env['cycles']
result = {'workload_bytes': env['workload_bytes'], 'cycles_per_guest': cycles, 'runs': []}
for row in rows:
    expected = env['workload_bytes']//4096 if row['mode'] == 'cram' else 0
    assert row['cycle_placed_pages'] == [expected]*cycles
    assert len(row['cycle_read_verify_seconds']) == cycles
    report = {'run': row['run'], 'cycles': []}
    for c in range(1, cycles+1):
        points = {s: row['checkpoints'][s if cycles == 1 else f'cycle{c}_{s}'] for s in ('idle','filled','placed','verified','released')}
        assert all(x['rollup']['Swap'] == 0 for samples in points.values() for x in samples)
        assert all(x['rollup']['AnonHugePages'] == 0 for samples in points.values() for x in samples)
        values = {s: {k: median(x['rollup'][k] for x in samples)/1024 for k in ('Rss','Pss')} for s,samples in points.items()}
        before = {m['address']:m for m in points['verified'][1]['mappings']}
        after = {m['address']:m for m in points['released'][1]['mappings']}
        changes = []
        for address in before.keys() | after.keys():
            a, b = before.get(address, {}), after.get(address, {})
            delta = b.get('Pss',0)-a.get('Pss',0)
            if delta:
                sample = b or a
                changes.append({'address': address, 'label': sample['label'], 'permissions': sample['permissions'],
                                'size_kib': sample['Size'], 'pss_delta_kib': delta,
                                'before_pss_kib': a.get('Pss',0), 'after_pss_kib': b.get('Pss',0)})
        changes.sort(key=lambda x: abs(x['pss_delta_kib']), reverse=True)
        cycle_report = {'cycle': c, 'stages_mib': values,
            'release_pss_growth_mib': values['released']['Pss']-values['verified']['Pss'],
            'store_payload_bytes': {s: points[s][1]['store']['store-payload-bytes'] for s in points},
            'largest_mapping_changes': changes[:10],
            'all_mapping_pss_delta_mib': sum(x['pss_delta_kib'] for x in changes)/1024}
        if env.get('allocator_diagnostics'):
            cycle_report['allocator_bytes'] = {s: points[s][1]['allocator'] for s in points}
        if env.get('trim_on_release'):
            trimmed = points['released'][1]['after_trim']
            assert all(x['rollup']['Swap'] == 0 and x['rollup']['AnonHugePages'] == 0 for x in trimmed)
            cycle_report['after_trim_mib'] = {k: median(x['rollup'][k] for x in trimmed)/1024 for k in ('Rss','Pss')}
            cycle_report['trim_pss_returned_mib'] = values['released']['Pss'] - cycle_report['after_trim_mib']['Pss']
            cycle_report['allocator_after_trim_bytes'] = points['released'][1]['allocator_after_trim']
        report['cycles'].append(cycle_report)
    result['runs'].append(report)
result['limitation'] = 'Diagnostic runs without a matched DRAM control; background reclamation affects totals.'
(args.directory/'release-summary.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
