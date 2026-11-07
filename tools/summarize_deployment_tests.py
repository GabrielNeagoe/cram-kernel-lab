"""Validate helper-test exits and redact guest build identity for public evidence."""
import argparse
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('input', type=Path)
parser.add_argument('--expected', type=int, required=True)
parser.add_argument('--prefix', required=True)
args = parser.parse_args()
raw = args.input.read_text(encoding='utf-8-sig')
raw = re.sub(r'\([^\s()]+@[^\s()]+\)', '(builder@lab)', raw)
raw = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', raw)
codes = [int(x) for x in re.findall(r'^TEST_EXIT=(\d+)$', raw, re.M)]
if len(codes) != args.expected or any(codes):
    raise SystemExit(f'Expected {args.expected} successful tests, found {codes}')
if 'QEMU_EXIT=0' not in raw or not re.search(r'^STUDY_DONE$', raw, re.M):
    raise SystemExit('Guest did not finish cleanly')
recovery = []
for match in re.finditer(r'^RECOVERY_SECONDS=(\d+)\nnode[^\n]*\n(2 [0-9 ]+)\n', raw, re.M):
    fields = [int(x) for x in match[2].split()]
    recovery.append({'seconds': int(match[1]), 'free': fields[-2], 'present': fields[-1]})
failures = re.search(r'^FINAL_PROMOTE_FAIL\n(\d+)\n', raw, re.M)
summary = {'helper_tests': len(codes), 'exit_codes': codes,
           'recovery': recovery, 'promotion_failures': int(failures[1]) if failures else None,
           'qemu_exit': 0}
Path('evidence', args.prefix + '.txt').write_text(raw, encoding='utf-8')
Path('evidence', args.prefix + '.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary))
