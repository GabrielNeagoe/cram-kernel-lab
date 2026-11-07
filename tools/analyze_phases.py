"""Publish sanitized capture evidence and classify only loss-free, paired phases."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
from classify_kvm_trace import classify

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('capture', type=Path)
parser.add_argument('--output-dir', type=Path)
args = parser.parse_args()
source = args.capture
dest = args.output_dir or Path(__file__).resolve().parents[1]/'evidence/phases'/source.name
dest.mkdir(parents=True, exist_ok=True)
for f in source.iterdir():
    if f.suffix in ('.txt', '.json'):
        text = re.sub(r'\([^()\s]+@[^)]+\)', '(builder@lab)', f.read_text())
        (dest/f.name).write_text(text, encoding='utf-8')
manifest = json.loads((dest/'manifest.json').read_text())
if manifest['dry_run']:
    print('Preserved dry-run evidence:', dest); raise SystemExit(0)
stats = json.loads((dest/'trace-stats.json').read_text())
loss = sum(int(n) for data in stats.values() for n in re.findall(r'^(?:overrun|commit overrun|dropped events):\s*(\d+)', data, re.M))
if loss:
    (dest/'analysis-rejected.txt').write_text(f'Trace loss counters total {loss}; not used for complete phase counts.\n')
    print('Rejected lossy capture:', loss); raise SystemExit(0)
serial = (dest/'serial.txt').read_text()
region = re.search(r'^REGION_START\s*\n(0x[0-9a-f]+)\s*\nREGION_SIZE\s*\n(0x[0-9a-f]+)', serial, re.M)
if not region: raise ValueError('Missing actual guest CXL region')
start, size = (int(x,16) for x in region.groups())
measures = {}
for name, ms, minor, major in re.findall(r'^MEASURE,([^,]+),([\d.]+),(\d+),(\d+)', serial, re.M):
    if name in measures: raise ValueError('Duplicate measurement')
    measures[name] = dict(elapsed_ms=float(ms), guest_minor=int(minor), guest_major=int(major))
active = None
phases = {}
rows = []
for line in (dest/'trace.txt').read_text().splitlines():
    marker = re.search(r'(\d+\.\d+): tracing_mark_write: CRAM_(BEGIN|END) (\S+)', line)
    if marker:
        timestamp, kind, name = marker.groups()
        if kind == 'BEGIN':
            if active is not None or name in phases: raise ValueError('Overlapping or duplicate phase')
            active = name; phases[name] = dict(begin=float(timestamp), events=[])
        else:
            if active != name: raise ValueError('Missing or mismatched phase marker')
            phases[name]['end'] = float(timestamp); active = None
    if 'kvm_page_fault:' in line:
        events, _ = classify(line, start, size)
        event = events[0]
        if event['tid'] not in manifest['qemu_tids']: raise ValueError('Unexpected QEMU thread')
        event['phase'] = active or 'between_phases'
        rows.append(event)
        if active: phases[active]['events'].append(event)
if active or list(phases) != manifest['phases']: raise ValueError('Phase list mismatch')
summaries = []
for name, phase in phases.items():
    counts = Counter(e['access'] for e in phase['events'])
    summaries.append(dict(phase=name, bracket_ms=round(1000*(phase['end']-phase['begin']),6),
                          **measures[name], events=len(phase['events']),
                          read=sum('READ' in e['access'].split('|') for e in phase['events']),
                          write=sum('WRITE' in e['access'].split('|') for e in phase['events']),
                          execute=sum('EXEC' in e['access'].split('|') for e in phase['events']),
                          cxl_events=sum(e['range']=='candidate_CXL' for e in phase['events'])))
for filename, data in [('phase-summary.csv',summaries), ('events.csv',rows)]:
    with (dest/filename).open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
summary=dict(trace_loss_counters=loss, cxl_start=hex(start), cxl_size=hex(size), total_events=len(rows),
             between_phase_events=sum(e['phase']=='between_phases' for e in rows),
             access=dict(Counter(e['access'] for e in rows)),
             cxl_events=sum(e['range']=='candidate_CXL' for e in rows), phases=summaries)
(dest/'analysis.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
