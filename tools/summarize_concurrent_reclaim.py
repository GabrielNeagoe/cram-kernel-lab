"""Validate concurrent reclaim records and retain budget failures as deployment blockers."""
import argparse,json,re,statistics
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
results={};envs=[]
for case in ('pilot/concurrent','pilot/budget','guarded','concurrent','disk-budget','oom'):
 d=a.directory/case
 r=json.loads((d/'measurements.json').read_text());e=json.loads((d/'environment.json').read_text());text=(d/'guest.txt').read_text();envs.append(e)
 assert re.search(r'^TEST_EXIT=0$',text,re.M)
 assert all(n[1:]==[4096,0] for n in r['neighbor'])
 assert all(s['Swap']==0 and s['AnonHugePages']==0 for s in r['host_samples_kib'].values())
 stats=r['stats'];out={'rounds':e['rounds'],'memory_limit_mib':e['target_memory_limit']/2**20,'zswap_allowance_mib':e['target_zswap_limit']/2**20}
 if case=='oom':
  assert re.search(r'^OOM_CONTAINMENT_PASS$',text,re.M)
  assert stats['oom_0']['oom_kill']>=1 and stats['oom_0']['oom_group_kill']>=1
  out.update(oom_kill_counter=stats['oom_0']['oom_kill'],oom_group_kill_counter=stats['oom_0']['oom_group_kill'],neighbor_integrity='PASS')
 else:
  assert re.search(r'^CHURN_INTEGRITY_PASS$',text,re.M)
  rounds=r['rounds'];requests=r['requests'];count=e['rounds']
  assert len(rounds)==count and len(requests)==count*8
  assert [n['round'] for n in rounds]==list(range(1,count+1))
  assert all(sorted(q['request'] for q in requests if q['round']==i and q['worker']==w)==[0,1,2,3] for i in range(1,count+1) for w in (0,1))
  assert all(rounds[i][k]>(rounds[i-1][k] if i else 0) for i in range(count) for k in ('pages0','pages1'))
  assert all(q['result']>=0 or q['errno']==11 for q in requests)
  assert all(any(q['progress']>0 for q in requests if q['round']==i) for i in range(1,count+1))
  assert len(re.findall(r'^REMAP round=\d+ PASS$',text,re.M))==count//4
  assert all(s['oom_kill']==0 for s in stats.values())
  final=stats['released_'+str(count)];assert final['pool_bytes']==0 and final['stored_pages']==0
  peak=max(s['memory.peak'] for s in stats.values());assert peak==r['peak_memory_charge']
  pool=max([n['peak_sample_pool'] for n in rounds]+[s['pool_bytes'] for s in stats.values()]);assert pool==r['max_sampled_pool']
  z=max([n['peak_sample_zswap'] for n in rounds]+[s['memory.zswap.current'] for s in stats.values()]);assert z==r['max_sampled_zswap']
  io={stage:(int(rd),int(wr)) for stage,_,rd,wr in re.findall(r'^IO stage=(\w+) round=(\d+) read=(\d+) written=(\d+)$',text,re.M)}
  lat=sorted(q['seconds'] for q in requests)
  out.update(requests=len(requests),overlapping_requests=sum(q['progress']>0 for q in requests),remap_cycles=count//4,
   churn_phase_seconds=sum(n['seconds'] for n in rounds),pages_verified_and_rewritten=rounds[-1]['pages0']+rounds[-1]['pages1'],
   peak_memory_charge_mib=peak/2**20,memory_limit_excess_bytes=max(0,peak-e['target_memory_limit']),
   max_sampled_pool_mib=pool/2**20,max_sampled_zswap_mib=z/2**20,zswap_allowance_excess_bytes=max(0,z-e['target_zswap_limit']),
   sampled_pool_envelope_4mib_pass=pool<=4*2**20,memory_max_events=max(s['max'] for s in stats.values()),
   reclaim_seconds_median=statistics.median(lat),reclaim_seconds_p95=lat[int(.95*(len(lat)-1))],reclaim_seconds_max=max(lat),
   post_fill_disk_reads_mib=(io['released'][0]-io['filled'][0])*512/2**20,post_fill_disk_writes_mib=(io['released'][1]-io['filled'][1])*512/2**20,
   released_target_charge_bytes=final['memory.current'],released_pool_bytes=final['pool_bytes'],integrity='PASS')
  if case in ('pilot/budget','guarded','disk-budget'):assert out['memory_max_events']>0
  if case=='disk-budget':assert z==0 and pool==0
  else:assert pool>0 and max(v['zswpin'] for v in stats.values())>0 and max(v['zswpout'] for v in stats.values())>0
  if case in ('pilot/budget','guarded','disk-budget'):assert out['post_fill_disk_reads_mib']>0 and out['post_fill_disk_writes_mib']>0
 results[case]=out
for key in ('kernel_sha256','qemu_sha256','accelerator'):
 assert len({e[key] for e in envs})==1,key
assert len({e['workload_sha256'] for e in envs[2:]})==1
summary={'cases':results,'compression_deployment_gate':'BLOCKED: strict nonzero physical compressed-memory budget not enforced',
 'interpretation':'Functional integrity/isolation PASS is separate from budget enforcement. Pool samples can miss transient peaks; memory.peak records charged memory rather than all physical overhead. TCG timings are observations only.'}
(a.directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
