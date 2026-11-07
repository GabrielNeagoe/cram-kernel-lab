"""Extract recorded benchmark values and redact local build identity for sharing."""
import csv
import json
import re
import statistics
from pathlib import Path

root = Path(__file__).resolve().parents[1]
text = (root/'private/guest-verification-raw.txt').read_text(encoding='utf-8-sig')
text = re.sub(r'\([^()\s]+@[^)]+\)', '(builder@lab)', text)
(root/'evidence/guest-verification.txt').write_text(text, encoding='utf-8')
# Kernel messages can share a serial line with a benchmark record.
text = re.sub(r'\[\s*\d+\.\d+\].*', '', text)
rows = []
run = None
for line in text.splitlines():
    m = re.fullmatch(r'BENCH_RUN=(\d+)', line)
    if m: run = int(m[1])
    m = re.fullmatch(r'RESULT,([^,]+),([\d.]+),([\d.]+)', line)
    if m:
        rows.append(dict(run=run, operation=m[1], elapsed_ms=float(m[2]), ns_per_page_visit=float(m[3])))
assert len(re.findall(r'^BENCH_EXIT=0$', text, re.M)) == 5
assert len(re.findall(r'^INTEGRITY,PASS$', text, re.M)) == 5
assert len(re.findall(r'^DEMOTED_PAGES,8192$', text, re.M)) == 5
assert 'QEMU_EXIT=0' in text
summary = {}
for op in dict.fromkeys(r['operation'] for r in rows):
    values = [r['elapsed_ms'] for r in rows if r['operation'] == op]
    assert len(values) == 5
    summary[op] = dict(n=len(values), median_ms=statistics.median(values), min_ms=min(values), max_ms=max(values))
with (root/'evidence/benchmark-results.csv').open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
(root/'evidence/benchmark-summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary, indent=2))
