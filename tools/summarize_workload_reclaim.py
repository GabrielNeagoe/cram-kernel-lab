"""Validate and compare observed cgroup reclaim, frame state, and physical memory."""
import argparse,json,statistics
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);args=p.parse_args()
results={};envs=[]
for case in ('compressed','disk','bounded','random'):
    d=args.directory/case
    env=json.loads((d/'environment.json').read_text());envs.append(env)
    r=json.loads((d/'measurements.json').read_text())
    assert r['integrity']=='PASS' and env['exclusive'] and env['page_reporting']
    f=r['fields'];pg=r['pages'];samples=r['host_samples_kib']
    assert pg['reclaimed']['target']['swapped']==8192 and pg['reclaimed']['target']['present']==0
    assert pg['reclaimed']['neighbor']['present']==4096 and pg['reclaimed']['neighbor']['swapped']==0
    assert f['reclaimed']['target']['anon']==0
    assert f['released']['zswap']['stored_pages']==0 and f['released']['zswap']['pool_bytes']==0
    for stage in samples:
        assert all(x['Swap']==0 and x['AnonHugePages']==0 for x in samples[stage])
        assert f[stage]['target']['memory.zswap.current']<=env['target_zswap_limit']+4096
        assert f[stage]['target']['memory.current']<=env['target_memory_limit']
        assert f[stage]['target']['oom_kill']==0
    host={stage:{k:statistics.median(x[k] for x in vals)/1024 for k in ('Rss','Pss')} for stage,vals in samples.items()}
    results[case]={
        'pattern':env['pattern'],'accelerator':env['accelerator'],
        'target_charge_before_mib':f['filled']['target']['memory.current']/2**20,
        'target_charge_after_mib':f['reclaimed']['target']['memory.current']/2**20,
        'target_charge_reduction_mib':(f['filled']['target']['memory.current']-f['reclaimed']['target']['memory.current'])/2**20,
        'target_zswap_charge_mib':f['reclaimed']['target']['memory.zswap.current']/2**20,
        'configured_zswap_limit_bytes':env['target_zswap_limit'],
        'strict_zswap_cap_pass':max(f[s]['target']['memory.zswap.current'] for s in f)<=env['target_zswap_limit'],
        'observed_zswap_cap_excess_bytes':max(0,max(f[s]['target']['memory.zswap.current']-env['target_zswap_limit'] for s in f)),
        'zsmalloc_pool_mib':f['reclaimed']['zswap']['pool_bytes']/2**20,
        'stored_pages':f['reclaimed']['zswap']['stored_pages'],
        'target_swapcache_bytes':f['reclaimed']['target']['swapcached'],
        'host_stages_mib':host,
        'host_reduction_mib':{k:host['filled'][k]-host['reclaimed'][k] for k in ('Rss','Pss')},
        'guest_free_increase_mib':(f['reclaimed']['guest']['MemFree']-f['filled']['guest']['MemFree'])/1024,
        'swap_write_mib':(f['reclaimed']['disk']['written_sectors']-f['filled']['disk']['written_sectors'])*512/2**20,
        'restore_disk_read_mib':(f['restored']['disk']['read_sectors']-f['reclaimed']['disk']['read_sectors'])*512/2**20,
        'original_frame_flags_after_reclaim':pg['reclaimed']['target'],
        'neighbor_resident_pages_after_reclaim':pg['reclaimed']['neighbor']['present'],
        'released_target_charge_bytes':f['released']['target']['memory.current'],
        'reclaim_attempts':r['reclaim_attempts'],'clock_warning':r['clock_warning'],
        'integrity':'PASS'}
for key in ('kernel_sha256','qemu_sha256','initrd_sha256','workload_sha256','accelerator','settle_seconds'):
    assert len({env[key] for env in envs})==1,key
assert results['compressed']['swap_write_mib']==0 and results['compressed']['restore_disk_read_mib']==0
assert results['compressed']['stored_pages']==8192
for case in ('disk','bounded','random'):
    assert results[case]['swap_write_mib']>0 and results[case]['restore_disk_read_mib']>0
summary={'cases':results,'scope':'One guest per case; Linux-host QEMU RSS/PSS, not Windows physical-memory savings or timing benchmark; cgroup charge includes compressed memory, not just application RSS.'}
(args.directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
