"""Cold-start the measured policy and restore the original policy in finally."""
import argparse, ctypes, json, os, re, subprocess as sp, sys, tempfile, time
from pathlib import Path

p=argparse.ArgumentParser()
for key in ('vmrun','vmx','plink','destination','key','config','out','backup'):
    p.add_argument('--'+key,required=True)
p.add_argument('--standard-counters',action='store_true')
p.add_argument('--run-prefix',default='')
p.add_argument('--preback',action='store_true',help='Use a pre-touched finite pool; cold-start each arm for matched initial backing.')
p.add_argument('--physical-window',action='store_true')
p.add_argument('--all-frames',action='store_true')
p.add_argument('--markers',action='store_true')
p.add_argument('--pair',action='store_true',help='Run one full-match arm and one closed-gate control.')
p.add_argument('--reuse-guard',action='store_true',help='Resume after a verified guarded boot under the original policy.')
a=p.parse_args(); out=Path(a.out); cfg=Path(a.config); vmx=Path(a.vmx)
assert not a.physical_window or a.all_frames
assert not a.all_frames or (a.markers and a.pair and a.preback and not a.standard_counters)
ssh=[a.plink,'-batch','-ssh',a.destination,'-P','22','-i',a.key]
remote='/srv/cram-lab/frame-study-20261107' if a.markers else '/srv/cram-lab/full-request-20261106'
guard='/srv/cram-lab/startup-balloon-20261106'
check='/srv/cram-lab/host-policy-20261106/one-boot-host-policy.py --check'
normal={'useRecommendedLockedMemSize':'TRUE','allVMMemoryLimit':'12288','minVmMemPct':'50'}
low=dict(normal,useRecommendedLockedMemSize='FALSE',minVmMemPct='25')

def save(name,obj):
    (out/name).write_text(json.dumps(obj,indent=2)+'\n',encoding='utf-8')
def cmd(args,timeout=60):
    return sp.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout.strip()
def guest(s,timeout=20): return cmd(ssh+[s],timeout)
def vms(): return cmd([a.vmrun,'-T','ws','list']).splitlines()[1:]
def until(fn,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if fn(): return
        time.sleep(2)
    raise TimeoutError('condition did not become ready')
def policy(data,expected,new):
    for k,v in expected.items():
        pattern=rb'(?m)^(prefvmx\.'+k.encode()+rb'\s*=\s*")([^"\r\n]+)(")'
        matches=list(re.finditer(pattern,data));assert len(matches)==1 and matches[0][2]==v.encode(), k
        start,end=matches[0].span(2);data=data[:start]+new[k].encode()+data[end:]
    return data
def set_policy(expected,new):
    assert not vms(),'all VMs must be off for global policy changes'
    original=cfg.read_bytes();changed=policy(original,expected,new)
    # ReplaceFileW preserves the destination file's Windows access controls.
    with tempfile.NamedTemporaryFile(dir=cfg.parent,delete=False) as f:
        temp=Path(f.name);f.write(changed);f.flush();os.fsync(f.fileno())
    try:
        assert cfg.read_bytes()==original
        api=ctypes.WinDLL('kernel32',use_last_error=True).ReplaceFileW
        if not api(str(cfg),str(temp),None,0,None,None): raise ctypes.WinError(ctypes.get_last_error())
        assert cfg.read_bytes()==changed
    finally: temp.unlink(missing_ok=True)
def poweroff():
    assert vms()==[str(vmx)]
    guest('sudo -n python3 '+check+' && sudo -n grub-reboot cram-arena-r2 && sudo -n grub-editenv - list | grep -Fx next_entry=cram-arena-r2 && sudo -n systemctl poweroff')
    until(lambda:not vms(),240)
def boot(mib,pct,label):
    assert not vms();cmd([a.vmrun,'-T','ws','start',str(vmx)],90)
    def ready():
        try: return guest('uname -r',6)=='7.2.0-rc2-cram-vm-arena-r2+'
        except (sp.SubprocessError,OSError): return False
    until(ready,120)
    log=(vmx.parent/'vmware.log').read_text(errors='replace')
    rows=[l.split('MemSched:',1)[1].strip() for l in log.splitlines() if 'MemSched:' in l]
    limits={int(m[1]) for row in rows if (m:=re.search(r'locked Page Limit:.*config (\d+)',row))}
    percentages={int(m[1]) for row in rows if (m:=re.match(r'minmempct (\d+)',row))}
    assert limits=={mib*256} and percentages=={pct},(limits,percentages)
    assert guest('cat /sys/module/zswap/parameters/enabled /sys/module/zswap/parameters/cram_arena_mb /sys/module/zswap/parameters/cram_arena_fail_every; test ! -e /sys/module/vmw_balloon && echo guarded').splitlines()==['Y','4','0','guarded']
    save(label+'.json',dict(verified_mib=mib,min_percent=pct,guarded=True,cram_mib=4,scheduler=rows))
    print('Verified startup:',mib,'MiB,',pct,'percent; guard active',flush=True)

assert ((out/'monitor-ready').exists() or a.standard_counters) and not (out/'start').exists()
assert vms()==[str(vmx)]; initial=cfg.read_bytes();policy(initial,normal,normal)
with Path(a.backup).open('xb') as f:f.write(initial)
installed=changed=off=False;observer=None
try:
    if a.reuse_guard and guest('if test -e /etc/modprobe.d/cram-startup-guard.conf; then echo present; else echo absent; fi')=='present':
        assert guest('cat /etc/modprobe.d/cram-startup-guard.conf; test ! -e /sys/module/vmw_balloon && echo guarded')=='blacklist vmw_balloon\ninstall vmw_balloon /bin/false\nguarded'
        assert guest('sudo -n sha256sum /boot/initrd.img-7.2.0-rc2-cram-vm-arena-r2+').split()[0]=='59af87f7827f5b8dedd83caaf1fac348bcca50c7eca277ea4f0bc35ebdf90998'
    else: print(guest('sudo -n python3 '+guard+'/install-startup-guard.py'),flush=True)
    installed=True
    poweroff();off=True
    set_policy(normal,low);changed=True;boot(12288,25,'low-startup');off=False
    (out/'start').touch()
    def has_metadata(directory):
        file=directory/'windows-page-metadata.jsonl'
        if not file.exists() or not file.stat().st_size:return False
        row=json.loads(file.read_text().splitlines()[0]);assert any(v['kind']=='vmem' for v in row['regions']);return True
    if not a.standard_counters and not a.preback: until(lambda:has_metadata(out/'host-pages'),30)
    arms=('full1','control') if a.pair else ('full1','control','full2')
    save('measurement-mode.json',dict(working_set=True,windows_available=True,backing_metadata=not a.standard_counters,arms=arms))
    for index,name in enumerate(arms):
        if a.preback and index:
            (obsdir/'stop').touch();observer.wait(timeout=25);observer=None
            if not a.standard_counters:
                (metadir/'stop').touch();until(lambda:(metadir/'stopped').exists(),15)
            poweroff();boot(12288,25,'low-startup-'+name)
        if a.preback and not a.standard_counters:
            metadir=out/('host-pages-'+name);metadir.mkdir();(metadir/'start').touch()
            until(lambda:has_metadata(metadir),30)
        if observer is None:
            obsdir=out/('observer-'+name if a.preback else 'observer')
            observer=sp.Popen([sys.executable,str(Path(__file__).with_name('observe_vmware_host_request.py')),'--plink',a.plink,'--destination',a.destination,'--key',a.key,'--out',str(obsdir),'--seconds','1200','--allow-targeted','--allow-unloaded'],stdout=sp.DEVNULL)
            until(lambda:(obsdir/'samples.jsonl').exists() and (obsdir/'samples.jsonl').stat().st_size>0,20)
        assert not a.run_prefix or a.run_prefix.isalnum()
        guest_name=a.run_prefix+name
        assert observer.poll() is None
        print('Starting',name,flush=True)
        call='sudo -n systemd-run --quiet --collect --pipe --wait --unit=cram-full-trial -p RuntimeMaxSec='+('770' if a.physical_window else '360')+' -p "ExecStopPost=/usr/bin/python3 '+remote+'/test_startup_host_request.py --safe-cleanup" /usr/bin/python3 '+remote+'/test_full_balloon_request.py --run-name '+guest_name+(' --hold-only' if name=='control' else '')+(' --preback' if a.preback else '')+(' --markers' if a.markers else '')+(' --all-frames' if a.all_frames else '')+(' --physical-window' if a.physical_window else '')
        try: print(guest(call,800 if a.physical_window else 400),flush=True)
        except sp.CalledProcessError as e:
            print(e.stderr[-2000:],flush=True);raise
        finally:
            folder=out/name;folder.mkdir(exist_ok=True)
            for fname in ('events.jsonl','result.json'):
                data=guest('sudo -n cat '+remote+'/'+guest_name+'/'+fname)
                (folder/fname).write_text(data+'\n',encoding='utf-8')
        result=json.loads((folder/'result.json').read_text());assert result['outcome']=='verified'
        assert result['verified']['zswpwb']==result['filled']['zswpwb'] and result['verified']['oom_kills']==0
finally:
    (out/'stop').touch()
    for sub in ('observer','host-pages'):
        if (out/sub).exists(): (out/sub/'stop').touch()
    if observer:
        (obsdir/'stop').touch()
        try: observer.wait(timeout=25)
        except sp.TimeoutExpired: observer.terminate();observer.wait(timeout=10)
    if changed:
        if vms():poweroff()
        set_policy(low,normal);boot(114485,50,'restored-startup');off=False
    elif off:boot(114485,50,'restored-startup');off=False
    if installed:
        cleanup=json.loads(guest('sudo -n python3 '+guard+'/cleanup-startup-guard.py'))
        assert cleanup['startup_guard_removed'] and cleanup['counters']['target']==cleanup['counters']['current']==0
        save('cleanup.json',cleanup);print('Verified rollback and restored stock driver.',flush=True)
