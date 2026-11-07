"""Concurrent mutation/reclaim, sustained churn and budget containment in disposable guests."""
from pathlib import Path
import base64,argparse,gzip,subprocess,selectors,os,time,re,json,tempfile,hashlib,sys,statistics

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--case',choices=('concurrent','budget','guarded','disk-budget','oom','arena-full','ancestor','rollback','swapoff'),required=True)
p.add_argument('--rounds',type=int,default=120)
p.add_argument('--arena-mib',type=int,default=4)
args=p.parse_args()
assert args.case=="ancestor","This harness tests two siblings under an ancestor quota"
base=Path(__file__).resolve().parent;lab=Path('/srv/cram-lab')
out=base/args.case;out.mkdir(exist_ok=False)
for proc in Path('/proc').glob('[0-9]*'):
    try:
        if (proc/'comm').read_text().startswith('qemu-system'):raise RuntimeError('Other QEMU guest running')
    except (FileNotFoundError,PermissionError):pass
kernel=lab/'build-linux-cram-arena/arch/x86/boot/bzImage'
qemu=lab/'build-qemu-compressed/qemu-system-x86_64'
overlay=base/'overlay';(overlay/'bin').mkdir(parents=True,exist_ok=True)
subprocess.run(['gcc','-static','-O2','-Wall','-Wextra','-Werror',str(base/'reservation-siblings.c'),'-o',str(overlay/'bin/reservation-siblings')],check=True)
initrd=out/'churn.cpio.gz'
if not initrd.exists():
    paths=subprocess.check_output(['find','.','-print0'],cwd=overlay)
    archive=subprocess.run(['cpio','--null','-o','-H','newc'],cwd=overlay,input=paths,stdout=subprocess.PIPE,check=True).stdout
    initrd.write_bytes((lab/'images/initramfs-cram-preflush.cpio.gz').read_bytes()+gzip.compress(archive))
limit=(0 if args.case in ('oom','disk-budget') else 64 if args.case=='arena-full' else 1 if args.case in ('guarded','rollback') else 3)*1024*1024
parentlimit=512*1024 if args.case=='ancestor' else 64*1024*1024
inject=7 if args.case=='rollback' else 0
memlimit={'concurrent':64,'budget':24,'guarded':24,'disk-budget':24,'oom':12,'arena-full':64,'ancestor':64,'rollback':24,'swapoff':64}[args.case]*1024*1024
swaplimit=0 if args.case=='oom' else 64*1024*1024
accelerator='kvm' if os.access('/dev/kvm',os.R_OK|os.W_OK) else 'tcg'
env={'case':args.case,'rounds':args.rounds,'zswap_enabled':True,'target_zswap_limit':limit,
     'storage_backend':'fixed_arena_lz4','arena_mib':args.arena_mib,'parent_limit':parentlimit,'inject_every':inject,'target_memory_limit':memlimit,'target_swap_limit':swaplimit,'compressed_physical_envelope':4*1024*1024,
     'accelerator':accelerator,'host_kernel':os.uname().release,'sample_interval_seconds':0.02,
     'kernel_sha256':hashlib.sha256(kernel.read_bytes()).hexdigest(),
     'qemu_sha256':hashlib.sha256(qemu.read_bytes()).hexdigest(),
     'initrd_sha256':hashlib.sha256(initrd.read_bytes()).hexdigest(),
     'workload_sha256':hashlib.sha256((overlay/'bin/reservation-siblings').read_bytes()).hexdigest(),
     'host_thp_disabled_for_child':True,'page_reporting':True,'exclusive':True}
(out/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
def memory(pid):
    data=Path(f'/proc/{pid}/smaps_rollup').read_text()
    return {k:int(v) for k,v in re.findall(r'^(\w+):\s+(\d+) kB$',data,re.M)}
with tempfile.TemporaryDirectory(prefix='scratch-',dir=base) as tmp:
    disk=Path(tmp)/'scratch.raw'
    with disk.open('xb') as f:f.truncate(128*1024*1024)
    command=[sys.executable,str(base/'exec_without_thp.py'),str(qemu),'-L',str(lab/'qemu/pc-bios'),
             '-machine','q35','-accel',accelerator,'-cpu','host' if accelerator=='kvm' else 'max','-smp','2','-m','768M',
             '-kernel',str(kernel),'-initrd',str(initrd),'-append',f'console=ttyS0 zswap.cram_arena_mb={args.arena_mib} zswap.cram_arena_fail_every={inject} rdinit=/init panic=-1 zswap.enabled=1 page_reporting.page_reporting_order=0',
             '-drive',f'file={disk},format=raw,if=virtio,cache=none',
             '-device','virtio-balloon-pci,bus=pcie.0,free-page-reporting=on',
             '-nographic','-monitor','none','-nic','none','-no-reboot']
    # /dev/vda is exclusively the new scratch image in this disposable guest.
    script=f'''mount -t debugfs debugfs /sys/kernel/debug; mkdir -p /sys/fs/cgroup; mount -t cgroup2 none /sys/fs/cgroup; echo +memory > /sys/fs/cgroup/cgroup.subtree_control; mkdir /sys/fs/cgroup/budget /sys/fs/cgroup/neighbor; echo +memory > /sys/fs/cgroup/budget/cgroup.subtree_control; mkdir /sys/fs/cgroup/budget/target /sys/fs/cgroup/budget/sibling; echo 67108864 > /sys/fs/cgroup/budget/sibling/memory.max; echo 67108864 > /sys/fs/cgroup/budget/sibling/memory.swap.max; echo {limit} > /sys/fs/cgroup/budget/sibling/memory.zswap.max; echo {parentlimit} > /sys/fs/cgroup/budget/memory.zswap.max; echo 1 > /sys/module/zswap/parameters/enabled; echo 1024 > /proc/sys/vm/percpu_pagelist_high_fraction; echo {memlimit} > /sys/fs/cgroup/budget/target/memory.max; echo {swaplimit} > /sys/fs/cgroup/budget/target/memory.swap.max; echo {limit} > /sys/fs/cgroup/budget/target/memory.zswap.max; echo 1 > /sys/fs/cgroup/budget/target/memory.zswap.writeback; echo 1 > /sys/fs/cgroup/budget/target/memory.oom.group; /bin/busybox mkswap /dev/vda && /bin/busybox swapon /dev/vda && /bin/reservation-siblings {args.case} {args.rounds}; echo TEST_EXIT=$?; echo STUDY_DONE; poweroff -f\n'''
    payload=base64.b64encode(script.encode()).decode()
    chunks=[payload[i:i+192] for i in range(0,len(payload),192)]
    upload_index=-1; upload_deadline=0
    (out/'guest-script.sh').write_text(script)
    env['guest_script_sha256']=hashlib.sha256(script.encode()).hexdigest()
    (out/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
    proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
    sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
    data=bytearray();pending=b'';sent=False;samples={};deadline=time.monotonic()+900; last_sample=0
    try:
        while time.monotonic()<deadline:
            if sent and upload_index<=len(chunks) and time.monotonic()>upload_deadline:
                raise RuntimeError('Acknowledged guest script upload timed out')
            if time.monotonic()-last_sample>=1:
                samples[str(round(time.monotonic(),3))]=memory(proc.pid);last_sample=time.monotonic()
            if not sel.select(1):
                if proc.poll() is not None:break
                continue
            chunk=os.read(proc.stdout.fileno(),65536)
            if not chunk:break
            data.extend(chunk);pending+=chunk
            with (out/'guest-live.txt').open('ab') as live:live.write(chunk)
            if re.search(rb'BUG:|Oops:|Kernel panic|WARNING:',data[-32768:]):
                raise RuntimeError('Guest kernel fault; preserve log and stop disposable guest')
            if not sent and b'Interactive shell ready.' in data:
                proc.stdin.write(b'PS1=; /bin/busybox stty -echo && echo CRAM_SERIAL_READY\n');proc.stdin.flush();sent=True;upload_deadline=time.monotonic()+30
            if sent and upload_index<=len(chunks):
                clean=re.sub(rb'\[\s*\d+\.\d+\] [^\n]*\n',b'',bytes(data[-32768:])).replace(b'\r',b'')
                marker=b'CRAM_SERIAL_READY' if upload_index<0 else f'CRAM_UPLOAD_ACK_{upload_index}'.encode()
                if re.search(rb'(?:^|\n)(?:~ # |/ # )?'+marker+rb'\n',clean):
                    upload_index+=1
                    if upload_index<len(chunks):
                        mode='>' if upload_index==0 else '>>'
                        prefix='/bin/busybox dmesg -n 5; ' if upload_index==0 else ''
                        command_line=f"{prefix}printf '%s' '{chunks[upload_index]}' {mode} /tmp/cram-launch.b64; echo CRAM_UPLOAD_ACK_{upload_index}\n"
                    else:
                        command_line='/bin/busybox base64 -d /tmp/cram-launch.b64 > /tmp/cram-launch.sh && /bin/sh /tmp/cram-launch.sh\n';upload_index+=1
                    proc.stdin.write(command_line.encode());proc.stdin.flush();upload_deadline=time.monotonic()+30
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1)
                if line.startswith((b'ROUND ',b'REMAP ',b'CHURN_',b'OOM_')):
                    print(args.case+' '+line.decode(errors='replace').strip(),flush=True)
        proc.wait(timeout=5)
        assert proc.returncode==0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        sel.close()
        text=data.decode(errors='replace').replace('\r','')
        text=re.sub(r'\([^\s()]+@[^\s()]+\)','(builder@lab)',text)
        (out/'guest.txt').write_text(text)
        (out/'host-samples.json').write_text(json.dumps(samples,indent=2)+'\n')
assert re.search(r'^TEST_EXIT=0$',text,re.M)
assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',text)
assert all(s['Swap']==0 and s['AnonHugePages']==0 for s in samples.values())
# Kernel printk may interrupt a userspace serial line. Preserve raw guest.txt;
# remove complete timestamped printk records only for structured measurement parsing.
parse_text=re.sub(r'\[\s*\d+\.\d+\] [^\n]*\n','',text)
stats={}
for stage,roundnum,key,value in re.findall(r'^STAT stage=(\w+) round=(\d+) key=([\w.]+) value=(\d+)$',parse_text,re.M):
    stats.setdefault(stage+'_'+roundnum,{})[key]=int(value)
neighbor=[tuple(map(int,x)) for x in re.findall(r'^NEIGHBOR round=(\d+) present=(\d+) swapped=(\d+)$',parse_text,re.M)]
assert all(s['memory.max']==memlimit and s['memory.swap.max']==swaplimit and s['memory.zswap.max']==limit and s['memory.oom.group']==1 for s in stats.values())
assert neighbor and all(p==4096 and s==0 for _,p,s in neighbor)
record={'case':args.case,'stats':stats,'neighbor':neighbor,'host_samples_kib':samples}
if args.case=='oom':
    assert re.search(r'^OOM_CONTAINMENT_PASS$',parse_text,re.M)
    assert stats['oom_0']['oom_kill']>=1 and stats['oom_0']['oom_group_kill']>=1
else:
    assert re.search(r'^CHURN_INTEGRITY_PASS$',parse_text,re.M)
    rounds=[{k:float(v) if '.' in v else int(v) for k,v in re.findall(r'(\w+)=([\d.]+)',line)} for line in re.findall(r'^ROUND .+$',parse_text,re.M)]
    requests=[{k:float(v) if '.' in v else int(v) for k,v in re.findall(r'(\w+)=(-?[\d.]+)',line)} for line in re.findall(r'^RECLAIM .+$',parse_text,re.M)]
    assert len(rounds)==args.rounds and len(requests)==args.rounds*8
    assert all(set(q)=={'round','worker','request','result','errno','seconds','progress'} for q in requests)
    assert len(neighbor)==args.rounds+1+(args.case=='swapoff')
    assert all(s['oom_kill']==0 for s in stats.values())
    assert stats['released_'+str(args.rounds)]['arena_used_bytes']==0 and stats['released_'+str(args.rounds)]['stored_pages']==0
    assert all(any(q['round']==r and q['progress']>0 for q in requests) for r in range(1,args.rounds+1))
    peak=max(s['memory.peak'] for s in stats.values())
    pool=max([r['peak_sample_pool'] for r in rounds]+[s['pool_bytes'] for s in stats.values()])
    z=max([r['peak_sample_zswap'] for r in rounds]+[s['memory.zswap.current'] for s in stats.values()])
    record.update(rounds=rounds,requests=requests,peak_memory_charge=peak,max_sampled_pool=pool,max_sampled_zswap=z,
        configured_memory_limit=memlimit,observed_memory_limit_excess=max(0,peak-memlimit),
        observed_zswap_limit_excess=max(0,z-limit),sampled_pool_within_4mib_envelope=pool<=4*1024*1024,
        reclaim_seconds_max=max(q['seconds'] for q in requests),overlapping_requests=sum(q['progress']>0 for q in requests))
    if args.case in ('budget','guarded','disk-budget'):assert max(s['max'] for s in stats.values())>0,'memory.max pressure was not exercised'
    if args.case=='disk-budget':assert z==0 and all(s['arena_used_bytes']==0 for s in stats.values()),'zero-compression policy breached'
for stage,s in stats.items():
    assert s['arena_backing_bytes']==args.arena_mib*1024*1024
    assert s['arena_peak_bytes']+s['arena_overhead_bytes']<=s['arena_backing_bytes']
    assert s['memory.cram.reserved']<=limit and s['parent_reserved']<=parentlimit
if args.case!='oom':
    end=stats['released_'+str(args.rounds)]
    assert end['memory.cram.reserved']==0 and end['parent_reserved']==0 and end['arena_entries']==0
    assert re.search(r'^EMPTY_QUOTA_LOWER_PASS$',parse_text,re.M)
    if args.case!='disk-budget':
        assert end['arena_stores']>0 and end['arena_loads']>0
        assert len(re.findall(r'^LOWER_QUOTA .+$',parse_text,re.M))==2
    if args.case=='arena-full':assert end['arena_reject_full']>0
    if args.case=='rollback':assert end['arena_injected_failures']>0
    if args.case=='ancestor':assert end['arena_reject_quota']>0
    if args.case=='swapoff':assert re.search(r'^LIVE_SWAPOFF_PASS$',parse_text,re.M)
record['reservation_invariants']='PASS'
assert max(s['sibling_zswpout'] for s in stats.values())>0 and max(s['zswpout'] for s in stats.values())>0
assert all(s['parent_reserved']==s['memory.cram.reserved']+s['sibling_reserved'] for s in stats.values())
assert max(s['arena_peak_bytes'] for s in stats.values())<=parentlimit
assert end['sibling_reserved']==0
record['sibling_reservation_invariants']='PASS'
(out/'measurements.json').write_text(json.dumps(record,indent=2)+'\n')
print(args.case+' FUNCTIONAL_PASS',flush=True)
