"""Measure workload-scoped reclaim in isolated non-CXL feasibility guests."""
from pathlib import Path
import argparse,gzip,subprocess,selectors,os,time,re,json,tempfile,hashlib,sys,statistics

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--case',choices=('disk','compressed','bounded','random'),required=True)
p.add_argument('--settle',type=float,default=10)
args=p.parse_args()
base=Path(__file__).resolve().parent;lab=Path('/srv/cram-lab')
out=base/args.case;out.mkdir(exist_ok=False)
for proc in Path('/proc').glob('[0-9]*'):
    try:
        if (proc/'comm').read_text().startswith('qemu-system'):raise RuntimeError('Other QEMU guest running')
    except (FileNotFoundError,PermissionError):pass
kernel=lab/'build-linux-sw-feasibility/arch/x86/boot/bzImage'
qemu=lab/'build-qemu-compressed/qemu-system-x86_64'
overlay=base/'overlay';(overlay/'bin').mkdir(parents=True,exist_ok=True)
subprocess.run(['gcc','-static','-O2','-Wall','-Wextra','-Werror',str(base/'workload-reclaim.c'),'-o',str(overlay/'bin/workload-reclaim')],check=True)
initrd=base/'reclaim.cpio.gz'
if not initrd.exists():
    paths=subprocess.check_output(['find','.','-print0'],cwd=overlay)
    archive=subprocess.run(['cpio','--null','-o','-H','newc'],cwd=overlay,input=paths,stdout=subprocess.PIPE,check=True).stdout
    initrd.write_bytes((lab/'images/initramfs-cram-preflush.cpio.gz').read_bytes()+gzip.compress(archive))
enabled=args.case!='disk';limit=4*1024*1024 if args.case in ('bounded','random') else 64*1024*1024
pattern='random' if args.case=='random' else 'mixed'
accelerator='kvm' if os.access('/dev/kvm',os.R_OK|os.W_OK) else 'tcg'
env={'case':args.case,'pattern':pattern,'zswap_enabled':enabled,'target_zswap_limit':limit,
     'target_memory_limit':64*1024*1024,'target_swap_limit':64*1024*1024,
     'accelerator':accelerator,'host_kernel':os.uname().release,'settle_seconds':args.settle,
     'kernel_sha256':hashlib.sha256(kernel.read_bytes()).hexdigest(),
     'qemu_sha256':hashlib.sha256(qemu.read_bytes()).hexdigest(),
     'initrd_sha256':hashlib.sha256(initrd.read_bytes()).hexdigest(),
     'workload_sha256':hashlib.sha256((overlay/'bin/workload-reclaim').read_bytes()).hexdigest(),
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
             '-kernel',str(kernel),'-initrd',str(initrd),'-append','console=ttyS0 rdinit=/init panic=-1 zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=20 page_reporting.page_reporting_order=0',
             '-drive',f'file={disk},format=raw,if=virtio,cache=none',
             '-device','virtio-balloon-pci,bus=pcie.0,free-page-reporting=on',
             '-nographic','-monitor','none','-nic','none','-no-reboot']
    # /dev/vda is exclusively the new scratch image in this disposable guest.
    script=f'''mount -t debugfs debugfs /sys/kernel/debug; mkdir -p /sys/fs/cgroup; mount -t cgroup2 none /sys/fs/cgroup; echo +memory > /sys/fs/cgroup/cgroup.subtree_control; mkdir /sys/fs/cgroup/target /sys/fs/cgroup/neighbor; echo {int(enabled)} > /sys/module/zswap/parameters/enabled; echo 1024 > /proc/sys/vm/percpu_pagelist_high_fraction; echo 67108864 > /sys/fs/cgroup/target/memory.max; echo 67108864 > /sys/fs/cgroup/target/memory.swap.max; echo {limit} > /sys/fs/cgroup/target/memory.zswap.max; echo 1 > /sys/fs/cgroup/target/memory.zswap.writeback; /bin/busybox mkswap /dev/vda && /bin/busybox swapon /dev/vda && /bin/workload-reclaim {pattern}; echo TEST_EXIT=$?; echo STUDY_DONE; poweroff -f\n'''
    proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
    sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
    data=bytearray();pending=b'';sent=False;samples={};deadline=time.monotonic()+240
    try:
        while time.monotonic()<deadline:
            if not sel.select(1):
                if proc.poll() is not None:break
                continue
            chunk=os.read(proc.stdout.fileno(),65536)
            if not chunk:break
            data.extend(chunk);pending+=chunk
            if not sent and b'Interactive shell ready.' in data:
                proc.stdin.write(script.encode());proc.stdin.flush();sent=True
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1)
                match=re.fullmatch(rb'CHECKPOINT=(\w+)\r?',line)
                if match:
                    stage=match[1].decode();assert stage not in samples
                    time.sleep(args.settle)
                    samples[stage]=[]
                    for i in range(3):
                        samples[stage].append(memory(proc.pid));time.sleep(.15)
                    proc.stdin.write(b'GO\n');proc.stdin.flush()
                    print(f'{args.case} {stage}: PSS={statistics.median(s["Pss"] for s in samples[stage])/1024:.3f} MiB',flush=True)
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
assert re.search(r'^TEST_EXIT=0$',text,re.M) and re.search(r'^WORKLOAD_RECLAIM_PASS$',text,re.M)
assert re.findall(r'^INTEGRITY=(\w+) PASS$',text,re.M)==['neighbor','target']
assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',text)
assert set(samples)=={'filled','reclaimed','restored','released'}
assert all(s['Swap']==0 and s['AnonHugePages']==0 for group in samples.values() for s in group)
fields={}
for stage,scope,key,value in re.findall(r'^FIELD stage=(\w+) scope=(\w+) key=([\w.:]+) value=(\d+)$',text,re.M):
    fields.setdefault(stage,{}).setdefault(scope,{})[key.rstrip(':')]=int(value)
pages={}
for stage,group,values in re.findall(r'^PAGES stage=(\w+) group=(\w+) (.+)$',text,re.M):
    pages.setdefault(stage,{})[group]={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',values)}
assert pages['reclaimed']['target']['swapped']==8192
assert pages['reclaimed']['neighbor']['present']==4096 and pages['reclaimed']['neighbor']['swapped']==0
for stage in fields:
    assert fields[stage]['target']['oom_kill']==0
    assert fields[stage]['target']['memory.current']<=64*1024*1024
    # Characterize the pre-admission limit check; do not relabel it byte-exact.
    assert fields[stage]['target']['memory.zswap.current']<=limit+4096
record={'fields':fields,'pages':pages,'host_samples_kib':samples,'integrity':'PASS',
        'strict_zswap_cap_pass':all(fields[s]['target']['memory.zswap.current']<=limit for s in fields),
        'max_observed_zswap_cap_excess_bytes':max(0,max(fields[s]['target']['memory.zswap.current']-limit for s in fields)),
        'reclaim_attempts':re.findall(r'^RECLAIM .+$',text,re.M),
        'clock_warning':bool(re.search(r'clocksource:.*(Watchdog|unstable|timed out)',text))}
(out/'measurements.json').write_text(json.dumps(record,indent=2)+'\n')
print(args.case+' PASS',flush=True)
