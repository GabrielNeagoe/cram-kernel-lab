"""Summarize recovery observations without treating stale counters as allocations."""
import csv
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
raw = (root/'private/guest-recovery-raw.txt').read_text(encoding='utf-8-sig')
public = re.sub(r'\([^()\s]+@[^)]+\)', '(builder@lab)', raw)
(root/'evidence/guest-recovery.txt').write_text(public, encoding='utf-8')
text = re.sub(r'\[\s*\d+\.\d+\].*', '', public)
lines = text.splitlines()
records = []
stage = None
for line in lines:
    if line in ('BASELINE', 'BEFORE_STAT_REFRESH', 'AFTER_STAT_REFRESH') or re.fullmatch(r'RECOVERY_SECONDS=\d+', line):
        stage = line
    elif stage and re.fullmatch(r'2(?:\s+\d+){9}', line):
        values = list(map(int, line.split()))
        records.append(dict(stage=stage, free_pages=values[8], present_pages=values[9],
                            apparent_shortfall_pages=values[9]-values[8], balloon_pages=values[4], target_pages=values[5]))
        stage = None
summary = dict(benchmark_passes=len(re.findall(r'^INTEGRITY,PASS$', text, re.M)),
               benchmark_exit_zero=len(re.findall(r'^BENCH_EXIT=0$', text, re.M)),
               demotion_8192_runs=len(re.findall(r'^DEMOTED_PAGES,8192$', text, re.M)),
               refresh_exit_zero='REFRESH_EXIT=0' in lines, qemu_exit_zero='QEMU_EXIT=0' in lines,
               observations=records)
assert summary['benchmark_passes'] == summary['benchmark_exit_zero'] == summary['demotion_8192_runs'] == 5
assert summary['refresh_exit_zero'] and summary['qemu_exit_zero']
assert len(records) == 8, records
with (root/'evidence/recovery-observations.csv').open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(records[0])); w.writeheader(); w.writerows(records)
(root/'evidence/recovery-summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary, indent=2))
