"""Bounded outer-VM arena compression/readback check; no global policy changes."""
import json,mmap,os,subprocess,sys,time
from pathlib import Path
CG=Path('/sys/fs/cgroup/cram-arena-verification')
DBG=Path('/sys/kernel/debug/zswap')
KEYS=['arena_backing_bytes','arena_overhead_bytes','arena_used_bytes','arena_peak_bytes','arena_entries','arena_stores','arena_loads']
def snap():
 r={k:int((DBG/k).read_text()) for k in KEYS}
 r.update({k:int((CG/k).read_text()) for k in ['memory.current','memory.swap.current','memory.cram.reserved']})
 return r
if '--worker' in sys.argv:
 (CG/'cgroup.procs').write_text(str(os.getpid()))
 n=16*1024*1024
 a=mmap.mmap(-1,n,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS)
 for i in range(n//4096):a[i*4096:(i+1)*4096]=bytes([i%251])*4096
 # Linux UAPI MADV_PAGEOUT is 21; some Python builds omit its named constant.
 a.madvise(getattr(mmap,'MADV_PAGEOUT',21))
 time.sleep(2)
 r=snap();assert r['memory.cram.reserved']>0,r
 assert r['memory.swap.current']>0,r
 for i in range(n//4096):assert a[i*4096:(i+1)*4096]==bytes([i%251])*4096,i
 a.close()
 print(json.dumps({'during':r,'verified_bytes':n,'data_integrity':'PASS'}))
 sys.exit(0)
assert os.geteuid()==0,'Administrator authentication required'
assert os.uname().release=='7.2.0-rc2-cram-vm-arena-r2+'
assert Path('/sys/module/zswap/parameters/enabled').read_text().strip()=='Y'
assert Path('/sys/module/zswap/parameters/cram_arena_mb').read_text().strip()=='4'
assert Path('/sys/module/zswap/parameters/cram_arena_fail_every').read_text().strip()=='0'
assert 'memory' in Path('/sys/fs/cgroup/cgroup.subtree_control').read_text().split()
assert not CG.exists(),'Test cgroup already exists; inspect before reuse'
CG.mkdir()
try:
 for k,v in [('memory.max',64*1024*1024),('memory.swap.max',64*1024*1024),('memory.zswap.max',1024*1024),('memory.oom.group',1)]: (CG/k).write_text(str(v))
 before=snap()
 p=subprocess.run([sys.executable,__file__,'--worker'],capture_output=True,text=True,timeout=60)
 assert p.returncode==0,p.stderr
 result=json.loads(p.stdout)
 for _ in range(50):
  after=snap()
  if after['memory.cram.reserved']==0:break
  time.sleep(.1)
 assert after['memory.cram.reserved']==0,after
 assert after['arena_stores']>before['arena_stores'] and after['arena_loads']>before['arena_loads']
 for r in [before,result['during'],after]:
  assert r['arena_backing_bytes']==4194304 and r['arena_peak_bytes']+r['arena_overhead_bytes']<=4194304
  assert r['memory.cram.reserved']<=1048576
 events=dict(line.split() for line in (CG/'memory.events').read_text().splitlines());assert int(events['oom_kill'])==0
 result.update(before=before,after=after,result='PASS',kernel=os.uname().release,arena_mib=4,workload_limit_bytes=67108864,entry_quota_bytes=1048576,oom_kills=0)
 out=Path(__file__).with_name('outer-compression-verification.json');out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
finally:
 CG.rmdir()
