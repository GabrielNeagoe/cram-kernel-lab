"""Validate and summarize baseline and standalone-store memory measurements."""
from pathlib import Path
import json
import statistics

base=Path('evidence/ram-savings')
baseline=json.loads((base/'results/measurements.json').read_text())
store=json.loads((base/'store-results/measurements.json').read_text())
assert len(baseline)==18 and len(store)==9
assert len({(r['repeat'],r['pattern'],r['mode']) for r in baseline})==18
assert len({(r['repeat'],r['pattern']) for r in store})==9
def metric(row, stage, key='Pss', guest=True):
    return statistics.median((s['rollup'] if guest else s)[key] for s in row['checkpoints'][stage])/1024
for r in baseline:
    assert r['placed_pages']==(16384 if r['mode']=='cram' else 0)
    assert all(s['rollup'].get('Swap',0)==0 for samples in r['checkpoints'].values() for s in samples)
for r in store:
    assert r['metrics']['INTEGRITY']=='PASS'
    assert all(s.get('Swap',0)==0 for samples in r['checkpoints'].values() for s in samples)
summary={'input_mib':64,'baseline_runs':18,'store_runs':9,
         'clock_warnings':sum(r['clock_warning'] for r in baseline),
         'baseline':[], 'standalone_store':[]}
for pattern in ('repeat','mixed','random'):
    for mode in ('dram','cram'):
        rows=[r for r in baseline if r['pattern']==pattern and r['mode']==mode]
        assert len(rows)==3
        deltas=[metric(r,'placed')-metric(r,'filled') for r in rows]
        summary['baseline'].append({'pattern':pattern,'mode':mode,
            'placement_pss_delta_mib_median':statistics.median(deltas),
            'placement_pss_delta_mib_min':min(deltas),'placement_pss_delta_mib_max':max(deltas)})
    rows=[r for r in store if r['pattern']==pattern]
    summary['standalone_store'].append({'pattern':pattern,
        'filled_pss_mib_median':statistics.median(metric(r,'filled',guest=False) for r in rows),
        'stored_pss_mib_median':statistics.median(metric(r,'stored',guest=False) for r in rows),
        'saved_pss_mib_median':statistics.median(metric(r,'filled',guest=False)-metric(r,'stored',guest=False) for r in rows),
        'both_copies_pss_mib_median':statistics.median(metric(r,'both_copies',guest=False) for r in rows),
        'payload_bytes':int(rows[0]['metrics']['PAYLOAD_BYTES']),
        'index_bytes':int(rows[0]['metrics']['INDEX_BYTES']),
        'raw_blocks':int(rows[0]['metrics']['RAW_BLOCKS'])})
(base/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
