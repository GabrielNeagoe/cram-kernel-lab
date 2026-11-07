"""Classify trace-cmd text; EPT interpretation assumes Intel VMX violations."""
import argparse
import collections
import csv
import json
import re
from pathlib import Path

def classify(text, start, size):
    pattern = re.compile(r'(\S+)-(\d+)\s+\[(\d+)\].*?\s(\d+\.\d+):\s+kvm_page_fault:\s+vcpu\s+(\d+)\s+rip\s+(0x[\da-fA-F]+)\s+address\s+(0x[\da-fA-F]+)\s+error_code\s+(0x[\da-fA-F]+)')
    rows = []
    for line in text.splitlines():
        if 'kvm_page_fault:' not in line:
            continue
        m = pattern.search(line)
        if not m:
            raise ValueError('Unparsed fault event: ' + line)
        name, tid, cpu, timestamp, vcpu, rip, address, code = m.groups()
        addr, bits = int(address, 16), int(code, 16)
        rows.append(dict(task=name, tid=int(tid), host_cpu=int(cpu), timestamp=float(timestamp),
                         vcpu=int(vcpu), rip=rip, address=address, error_code=code,
                         access='|'.join(label for bit, label in ((0,'READ'),(1,'WRITE'),(2,'EXEC')) if bits & (1 << bit)) or 'NONE',
                         range='candidate_CXL' if start <= addr < start + size else 'outside_candidate_CXL'))
    summary = dict(total=len(rows), candidate_cxl_start=hex(start), candidate_cxl_size=hex(size),
                   range_provenance='Caller supplied; does not establish the historical capture mapping')
    for key in ('range','access','error_code','vcpu','tid'):
        summary[key] = dict(collections.Counter(row[key] for row in rows))
    summary['access_bit_counts'] = {label: sum(label in row['access'].split('|') for row in rows) for label in ('READ','WRITE','EXEC')}
    summary['events_per_second'] = dict(sorted(collections.Counter(int(row['timestamp']) for row in rows).items()))
    if rows:
        summary.update(first_timestamp=min(r['timestamp'] for r in rows), last_timestamp=max(r['timestamp'] for r in rows),
                       min_address=hex(min(int(r['address'],16) for r in rows)), max_address=hex(max(int(r['address'],16) for r in rows)))
    return rows, summary

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--cxl-start', type=lambda x:int(x,0), required=True)
    p.add_argument('--cxl-size', type=lambda x:int(x,0), required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.cxl_start < 0 or args.cxl_size <= 0:
        p.error('CXL start must be nonnegative and size positive')
    rows, summary = classify(args.input.read_text(encoding='utf-8-sig'), args.cxl_start, args.cxl_size)
    args.output.with_suffix('.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    if rows:
        with args.output.with_suffix('.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(json.dumps(summary, indent=2))
