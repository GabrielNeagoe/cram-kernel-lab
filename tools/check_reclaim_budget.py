"""Fail a proposed compression deployment when observed memory-budget checks fail.

This evidence gate cannot certify an absolute physical ceiling from sampled data.
It never installs a kernel or changes a VM setting.
"""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('record',type=Path)
p.add_argument('--pool-budget-bytes',type=int,default=4*1024*1024)
p.add_argument('--charge-budget-bytes',type=int,default=32*1024*1024)
p.add_argument('--require-absolute-ceiling',action='store_true')
a=p.parse_args()
r=json.loads(a.record.read_text());e=json.loads(a.record.with_name('environment.json').read_text())
fail=[]
if a.pool_budget_bytes<0 or a.charge_budget_bytes<=0:p.error('budgets must be nonnegative, with a positive charge budget')
if not r.get('rounds') or not r.get('requests'):fail.append('No completed churn evidence')
if r.get('max_sampled_pool',float('inf'))>a.pool_budget_bytes:fail.append('Observed physical pool exceeded the proposed budget')
if r.get('peak_memory_charge',float('inf'))>a.charge_budget_bytes:fail.append('Recorded peak memory charge exceeded the proposed budget')
if e.get('target_zswap_limit',0)==0:fail.append('Compression was disabled; this is fallback evidence only')
if a.require_absolute_ceiling:fail.append('No allocation-reservation proof for an absolute physical ceiling; sampled tests cannot certify it')
print(json.dumps({'observed_budget_checks_pass':not fail,'failures':fail,
 'scope':'Evidence gate only; not connected to the installation tooling. A pass without --require-absolute-ceiling is observational, not a hard bound.'},indent=2))
raise SystemExit(2 if fail else 0)
