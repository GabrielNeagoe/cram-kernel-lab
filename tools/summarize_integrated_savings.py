"""Validate an exclusive integrated-store comparison and summarize KiB counters."""
import argparse
import json
from pathlib import Path
from statistics import median

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory', type=Path)
p.add_argument('--unpaired', action='store_true', help='Summarize one CRAM diagnostic without claiming control adjustment')
p.add_argument('--pattern', choices=('repeat','mixed','random'), help='Select one pattern from a multi-pattern run')
args = p.parse_args()
env = json.loads((args.directory / 'environment.json').read_text())
rows = json.loads((args.directory / 'measurements.json').read_text())
if args.pattern:
    rows = [r for r in rows if r['pattern'] == args.pattern]
assert env['exclusive'] and env['compressed_store']
assert (len(rows) == 1 and rows[0]['mode'] == 'cram') if args.unpaired else (len(rows) == 2 and {r['mode'] for r in rows} == {'dram', 'cram'})
assert len({r['pattern'] for r in rows}) == 1
summary = {'workload_bytes': env['workload_bytes'], 'trials': []}
for row in rows:
    assert row['placed_pages'] == (env['workload_bytes'] // 4096 if row['mode'] == 'cram' else 0)
    stages = {}
    for stage, samples in row['checkpoints'].items():
        assert len(samples) == 3
        assert all(x['rollup']['Swap'] == 0 for x in samples)
        stages[stage] = {k: median(x['rollup'][k] for x in samples) / 1024 for k in ('Rss', 'Pss')}
    summary['trials'].append({
        'mode': row['mode'], 'pattern': row['pattern'], 'stages_mib': stages,
        'placed_minus_filled_mib': {k: stages['placed'][k] - stages['filled'][k] for k in ('Rss', 'Pss')},
        'verified_minus_filled_mib': {k: stages['verified'][k] - stages['filled'][k] for k in ('Rss', 'Pss')},
        'placed_pages': row['placed_pages'], 'read_verify_seconds': row['read_verify_seconds'],
        'clock_warning': row['clock_warning'],
        'verified_store': row['checkpoints']['verified'][-1]['store'],
    })
if not args.unpaired:
    control = next(r for r in summary['trials'] if r['mode'] == 'dram')
    treatment = next(r for r in summary['trials'] if r['mode'] == 'cram')
    summary['control_adjusted_savings_mib'] = {
        stage: {k: control[stage][k] - treatment[stage][k] for k in ('Rss', 'Pss')}
        for stage in ('placed_minus_filled_mib', 'verified_minus_filled_mib')
    }
summary['limitation'] = ('Unpaired diagnostic; no control-adjusted savings claim.' if args.unpaired else
                        'One matched pair; not a repeated savings estimate or hardware performance result.')
(args.directory / ('summary-'+args.pattern+'.json' if args.pattern else 'summary.json')).write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
