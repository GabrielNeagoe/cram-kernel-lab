"""Intersect traced QEMU discards with guest workload source frames."""
import argparse
import json
from pathlib import Path
import re

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory', type=Path)
p.add_argument('--run', default='1-repeat-cram')
p.add_argument('--prefix-bytes', type=int, help='Diagnostic lower bound on pre-demotion trace prefix')
args = p.parse_args()
log = (args.directory / (args.run + '.txt')).read_text()
source = set()
for addr, size in re.findall(r'^SOURCE_RANGE=(0x[0-9a-f]+) bytes=(\d+)$', log, re.M):
    source.update(range(int(addr, 16), int(addr, 16) + int(size), 4096))
assert source and 'INTEGRITY=PASS' in log
env = json.loads((args.directory / 'environment.json').read_text())
assert len(source) == env['workload_bytes'] // 4096, 'Incomplete source-frame output'
rows = json.loads((args.directory / 'measurements.json').read_text())
row = next(r for r in rows if r['run'] == args.run)
prefix = args.prefix_bytes
if prefix is None:
    prefix = row['checkpoints']['filled'][-1]['report_trace_bytes']
trace = (args.directory / (args.run + '-report.trace')).read_bytes()[prefix:].decode()
reported = set()
failures = 0
for addr, size, status in re.findall(r'gpa=(0x[0-9a-f]+) length=(0x[0-9a-f]+) status=(-?\d+)', trace):
    if int(status):
        failures += 1
    else:
        reported.update(source.intersection(range(int(addr, 16), int(addr, 16) + int(size, 16), 4096)))
summary = {'source_pages': len(source), 'source_pages_successfully_discarded_after_prefix': len(reported),
           'discard_failures_after_prefix': failures, 'trace_prefix_bytes': prefix,
           'prefix_is_checkpoint_aligned': args.prefix_bytes is None,
           'guest_source_states': re.findall(r'^SOURCE_STATE=\w+ buddy_heads=\d+ lru=\d+ anon=\d+$', log, re.M),
           'source_state_records_incomplete': len(re.findall(r'^SOURCE_STATE=', log, re.M)) - len(re.findall(r'^SOURCE_STATE=\w+ buddy_heads=\d+ lru=\d+ anon=\d+$', log, re.M)),
           'limitation': 'Successful discard does not prove pages remain nonresident; physical frames can be reused.'}
(args.directory / (args.run + '-reclamation.json')).write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
