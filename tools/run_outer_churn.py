"""120-round bounded concurrent arena test on the outer VM."""
from pathlib import Path
import datetime,hashlib,json,os,re,signal,subprocess,time
b=Path(__file__).resolve().parent;cg=Path('/sys/fs/cgroup/cram-outer');out=b/'outer-concurrent-120'
assert os.geteuid()==0
assert os.uname().release=='7.2.0-rc2-cram-vm-arena-r2+'
assert Path('/sys/module/zswap/parameters/enabled').read_text().strip()=='Y'
assert Path('/sys/module/zswap/parameters/cram_arena_mb').read_text().strip()=='4'
assert Path('/sys/module/zswap/parameters/cram_arena_fail_every').read_text().strip()=='0'
assert {0,1}.issubset(os.sched_getaffinity(0))
assert not cg.exists() and not out.exists()
assert 'memory' in Path('/sys/fs/cgroup/cgroup.subtree_control').read_text().split()
out.mkdir();cg.mkdir();created=[cg];proc=None
start=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
try:
 (cg/'memory.max').write_text(str(192*2**20));(cg/'memory.swap.max').write_text(str(128*2**20));(cg/'cgroup.subtree_control').write_text('+memory')
 for name in ['budget','neighbor']:
  p=cg/name;p.mkdir();created.append(p);(p/'memory.max').write_text(str(64*2**20))
 (cg/'budget/cgroup.subtree_control').write_text('+memory')
 target=cg/'budget/target';target.mkdir();created.append(target)
 for p in [cg/'budget',target]: (p/'memory.zswap.max').write_text(str(1024*1024))
 for k,v in [('memory.max',64*2**20),('memory.swap.max',64*2**20),('memory.oom.group',1)]: (target/k).write_text(str(v))
 env={'kernel':os.uname().release,'rounds':120,'target_memory_limit':64*2**20,'target_entry_quota':2**20,'parent_entry_quota':2**20,'arena_bytes':4*2**20,'source_sha256':hashlib.sha256((b/'outer-reservation-churn.c').read_bytes()).hexdigest(),'binary_sha256':hashlib.sha256((b/'outer-reservation-churn').read_bytes()).hexdigest(),'disk_counters':'whole sda device, includes unrelated VM I/O; not attributable to this workload'}
 (out/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
 with (out/'workload.txt').open('w') as f:
  proc=subprocess.Popen([str(b/'outer-reservation-churn'),'concurrent','120'],stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  rc=proc.wait(timeout=900)
 assert rc==0,rc
 log=(out/'workload.txt').read_text();assert 'CHURN_INTEGRITY_PASS' in log and 'EMPTY_QUOTA_LOWER_PASS' in log
 stats={}
 for stage,n,k,v in re.findall(r'^STAT stage=(\w+) round=(\d+) key=([\w.]+) value=(\d+)$',log,re.M):stats.setdefault(stage+'_'+n,{})[k]=int(v)
 end=stats['released_120']
 for s in stats.values():
  assert s['arena_backing_bytes']==4*2**20 and s['arena_peak_bytes']+s['arena_overhead_bytes']<=4*2**20
  assert s['memory.cram.reserved']<=2**20 and s['parent_reserved']<=2**20 and s['oom_kill']==0
 assert end['memory.cram.reserved']==end['parent_reserved']==0
 assert end['arena_stores']>stats['round_1']['arena_stores'] and end['arena_loads']>stats['filled_0']['arena_loads']
 neighbors=re.findall(r'^NEIGHBOR round=(\d+) present=(\d+) swapped=(\d+)$',log,re.M)
 assert len(neighbors)==121 and all(p=='4096' and s=='0' for _,p,s in neighbors)
 requests=[dict(re.findall(r'(\w+)=(-?[\d.]+)',line)) for line in re.findall(r'^RECLAIM .+$',log,re.M)]
 assert len(requests)==960
 assert all(any(int(q['round'])==i and int(q['progress'])>0 for q in requests) for i in range(1,121))
 assert len(re.findall(r'^REMAP round=\d+ PASS$',log,re.M))==30
 assert len(re.findall(r'^LOWER_QUOTA .* errno=16$',log,re.M))==2
 warnings=subprocess.check_output(['dmesg','--since',start],text=True)
 (out/'kernel-since-test.private.log').write_text(warnings)
 assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',warnings)
 result={'result':'PASS','rounds':120,'reclaim_calls':len(requests),'calls_with_writer_progress':sum(int(q['progress'])>0 for q in requests),'remaps':30,'neighbor_resident_pages':4096,'data_integrity':'PASS','storage_bound':'PASS','target_and_parent_cleanup':'PASS','kernel_warning_scan':'PASS','oom_kills':0,'stats':stats}
 (out/'measurements.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='stats'},indent=2))
finally:
 if proc is not None:
  try:os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  proc.wait()
 if (cg/'cgroup.kill').exists():(cg/'cgroup.kill').write_text('1')
 for p in reversed(created):
  for attempt in range(50):
   try:p.rmdir();break
   except OSError:
    if attempt==49:raise
    time.sleep(.1)
