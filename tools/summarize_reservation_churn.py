"""Validate fixed-arena allocation and quota invariants from recorded churn tests."""
from pathlib import Path
import argparse,json,re
p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path)
p.add_argument('--cases',nargs='+',default=['guarded','concurrent','ancestor','arena-full','rollback','swapoff','disk-budget','oom'],choices=['guarded','concurrent','ancestor','arena-full','rollback','swapoff','disk-budget','oom'])
a=p.parse_args()
result={};envs=[]
for case in a.cases:
 d=a.directory/case;e=json.loads((d/'environment.json').read_text());r=json.loads((d/'measurements.json').read_text());log=(d/'guest.txt').read_text();envs.append(e)
 assert r['reservation_invariants']=='PASS'
 assert re.search(r'^TEST_EXIT=0$',log,re.M) and not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',log)
 log=re.sub(r'\[\s*\d+\.\d+\] [^\n]*\n','',log)
 assert all(n[1:]==[4096,0] for n in r['neighbor'])
 assert all(s['Swap']==0 and s['AnonHugePages']==0 for s in r['host_samples_kib'].values())
 stats=r['stats'];backing=e['arena_mib']*2**20
 for s in stats.values():
  assert s['arena_backing_bytes']==backing and s['pool_bytes']==backing
  assert s['arena_overhead_bytes']+s['arena_peak_bytes']<=backing
  assert s['memory.cram.reserved']<=e['target_zswap_limit'] and s['parent_reserved']<=e['parent_limit']
 # This suite has one storing target cgroup. The persistent global entry high-water
 # mark therefore also checks its own quota and the ancestor's quota.
 assert max(s['arena_peak_bytes'] for s in stats.values())<=min(e['target_zswap_limit'],e['parent_limit'])
 out={'rounds':e['rounds'],'backing_bytes':backing,'fixed_overhead_bytes':next(iter(stats.values()))['arena_overhead_bytes'],
      'target_quota_bytes':e['target_zswap_limit'],'parent_quota_bytes':e['parent_limit'],
      'peak_entry_reservation_bytes':max(s['arena_peak_bytes'] for s in stats.values()),
      'peak_workload_charge_bytes':max(s['memory.peak'] for s in stats.values()),
      'workload_memory_max_bytes':e['target_memory_limit'],'integrity':'PASS','storage_budget':'PASS'}
 if case=='oom':
  assert re.search(r'^OOM_CONTAINMENT_PASS$',log,re.M) and stats['oom_0']['oom_kill']>0
  out.update(oom_kill_counter=stats['oom_0']['oom_kill'],oom_group_kill_counter=stats['oom_0']['oom_group_kill'])
 else:
  n=e['rounds'];end=stats['released_'+str(n)];requests=r['requests'];rounds=r['rounds']
  assert re.search(r'^CHURN_INTEGRITY_PASS$',log,re.M)
  assert re.search(r'^EMPTY_QUOTA_LOWER_PASS$',log,re.M)
  assert len(rounds)==n and len(requests)==n*8
  assert all(sorted(q['request'] for q in requests if q['round']==i and q['worker']==w)==[0,1,2,3] for i in range(1,n+1) for w in (0,1))
  assert all(any(q['progress']>0 for q in requests if q['round']==i) for i in range(1,n+1))
  assert all(rounds[i][k]>(rounds[i-1][k] if i else 0) for i in range(n) for k in ('pages0','pages1'))
  assert all(s['oom_kill']==0 for s in stats.values())
  assert all(end[k]==0 for k in ('arena_entries','arena_used_bytes','memory.cram.reserved','parent_reserved','stored_pages'))
  if case!='disk-budget':
   assert end['arena_stores']>0 and end['arena_loads']>0
   assert len(re.findall(r'^LOWER_QUOTA .* errno=16$',log,re.M))==2
   # Stores must continue after the first round: not a test that only passes by
   # exhausting a leaking reservation and permanently falling back to disk.
   assert end['arena_stores']>stats['round_1']['arena_stores']
  else:assert end['arena_stores']==0 and end['arena_peak_bytes']==0
  if case=='ancestor':assert end['arena_reject_quota']>0
  if case=='arena-full':assert end['arena_reject_full']>0
  if case=='rollback':assert end['arena_injected_failures']>0
  if case=='swapoff':assert re.search(r'^LIVE_SWAPOFF_PASS$',log,re.M)
  io={stage:(int(rd),int(wr)) for stage,_,rd,wr in re.findall(r'^IO stage=(\w+) round=(\d+) read=(\d+) written=(\d+)$',log,re.M)}
  out.update(requests=len(requests),overlapping_requests=sum(q['progress']>0 for q in requests),
      churn_seconds=sum(v['seconds'] for v in rounds),max_reclaim_seconds=max(q['seconds'] for q in requests),
      stores=end['arena_stores'],loads=end['arena_loads'],reject_full=end['arena_reject_full'],reject_quota=end['arena_reject_quota'],
      reject_incompressible=end['arena_reject_poor'],injected_failures=end['arena_injected_failures'],
      end_entry_bytes=end['arena_used_bytes'],end_workload_charge_bytes=end['memory.current'],
      post_fill_disk_read_bytes=(io['released'][0]-io['filled'][0])*512,post_fill_disk_write_bytes=(io['released'][1]-io['filled'][1])*512)
 result[case]=out
for k in ('kernel_sha256','qemu_sha256','workload_sha256','accelerator'):
 assert len({e[k] for e in envs})==1,k
summary={'cases':result,'fixed_storage_reservation_gate':'PASS','deployment_status':'NOT_DEPLOYED: experimental backend; storage-arena bound is distinct from total workload/VM memory',
 'scope':'Fixed backing includes payload slots, entry metadata, fragmentation, bitmap/hash index, LZ4 workspace and output scratch. Existing kernel infrastructure, memcg lifetime/accounting and debugfs are outside this storage-arena bound. One guest per configuration; TCG.'}
installation=a.directory/'installation-result.json'
if installation.exists():
 installed=json.loads(installation.read_text())
 assert installed['installed'] and installed['kernel_sha256']==envs[0]['kernel_sha256']
 summary['deployment_status']='INSTALLED: experimental boot option, stock default retained; actual VMware boot pending'
outer=a.directory/'outer-compression-verification.json'
if outer.exists():
 verified=json.loads(outer.read_text())
 assert installation.exists() and verified['result']=='PASS' and verified['kernel']==installed['version']
 summary['deployment_status']='BOOTED AND VERIFIED: 4 MiB arena active; bounded outer-VM compression/readback test PASS'
(a.directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
