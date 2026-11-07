"""Reference zswap tests in disposable guests, not a software CRAM implementation."""
from pathlib import Path
import gzip,subprocess,selectors,os,time,re,json,tempfile,hashlib,argparse

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--constrained-only',action='store_true')
args=parser.parse_args()

base=Path(__file__).resolve().parent
lab=Path('/srv/cram-lab')
kernel=lab/'build-linux-sw-feasibility/arch/x86/boot/bzImage'
qemu=lab/'build-qemu-compressed/qemu-system-x86_64'
overlay=base/'overlay';(overlay/'bin').mkdir(parents=True,exist_ok=True)
subprocess.run(['gcc','-static','-O2','-Wall','-Wextra','-Werror',str(base/'software-compression-feasibility.c'),'-o',str(overlay/'bin/sw-feasibility')],check=True)
paths=subprocess.check_output(['find','.','-print0'],cwd=overlay)
archive=subprocess.run(['cpio','--null','-o','-H','newc'],cwd=overlay,input=paths,stdout=subprocess.PIPE,check=True).stdout
initrd=base/('feasibility-constrained.cpio.gz' if args.constrained_only else 'feasibility.cpio.gz')
initrd.write_bytes((lab/'images/initramfs-cram-preflush.cpio.gz').read_bytes()+gzip.compress(archive))
results=[]
accelerator='kvm' if os.access('/dev/kvm',os.R_OK|os.W_OK) else 'tcg'
for enabled,cap in ([(True,1)] if args.constrained_only else [(False,10),(True,10)]):
    name='constrained' if cap==1 else ('enabled' if enabled else 'disabled')
    with tempfile.TemporaryDirectory(prefix='swap-reference-',dir=base) as tmp:
        disk=Path(tmp)/'scratch.raw'
        with disk.open('xb') as f:f.truncate(128*1024*1024)
        command=[str(qemu),'-L',str(lab/'qemu/pc-bios'),'-machine','q35','-accel',accelerator,'-cpu','host' if accelerator=='kvm' else 'max','-smp','2','-m','768M',
                 '-kernel',str(kernel),'-initrd',str(initrd),'-append',f'console=ttyS0 rdinit=/init panic=-1 zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent={cap}',
                 '-drive',f'file={disk},format=raw,if=virtio,cache=none','-nographic','-monitor','none','-nic','none','-no-reboot']
        p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
        sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
        data=bytearray();sent=False;deadline=time.monotonic()+180
        # /dev/vda is solely the new throwaway scratch disk attached above.
        script=f'echo {int(enabled)} > /sys/module/zswap/parameters/enabled; '+ 'mount -t debugfs debugfs /sys/kernel/debug; /bin/busybox mkswap /dev/vda && /bin/busybox swapon /dev/vda && { for pattern in repeat mixed random; do /bin/sw-feasibility "$pattern"; echo TEST_EXIT=$?; done; }; echo STUDY_DONE; poweroff -f\n'
        try:
            while time.monotonic()<deadline:
                if not sel.select(1):
                    if p.poll() is not None:break
                    continue
                chunk=os.read(p.stdout.fileno(),65536)
                if not chunk:break
                data.extend(chunk)
                if not sent and b'Interactive shell ready.' in data:
                    p.stdin.write(script.encode());p.stdin.flush();sent=True
            p.wait(timeout=5)
            text=data.decode(errors='replace').replace('\r','')
            text=re.sub(r'\([^\s()]+@[^\s()]+\)','(builder@lab)',text)
            (base/(name+'.txt')).write_text(text)
            assert p.returncode==0 and re.findall(r'^TEST_EXIT=(\d+)$',text,re.M)==['0']*3
            assert not re.search(r'BUG:|Oops:|Kernel panic|WARNING:',text)
            snapshots=[dict(stage=s,swapped=int(a),stored=int(b),pool_bytes=int(c)) for s,a,b,c in re.findall(r'^SNAPSHOT=(\w+) swapped=(\d+) stored=(\d+) pool_bytes=(\d+)$',text,re.M)]
            assert len(snapshots)==15
            assert re.findall(r'^PATTERN=(\w+) INTEGRITY=PASS FORK_COW=PASS$',text,re.M)==['repeat','mixed','random']
            for index,snapshot in enumerate(snapshots):
                if snapshot['stage']=='compressed':
                    assert snapshot['swapped']==4096
                    if cap==1 and index//5>0:
                        assert 0 <= snapshot['stored'] < 4096
                        assert snapshot['pool_bytes']<=8*1024*1024
                    else:
                        assert snapshot['stored']==(4096 if enabled else 0)
                if snapshot['stage']=='released':assert snapshot['stored']==0
            results.append({'zswap_enabled':enabled,'pool_limit_percent':cap,'snapshots':snapshots,'integrity':'PASS','fork_cow':'PASS','clock_warning':bool(re.search(r'clocksource:.*(Watchdog|unstable|timed out)',text))})
            print(name+' PASS',flush=True)
        finally:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            sel.close()
record={'scope':'Unmodified zswap reference mechanism, not a software CRAM implementation or matched host RSS/PSS study',
        'accelerator':accelerator,
        'kernel_sha256':hashlib.sha256(kernel.read_bytes()).hexdigest(),'qemu_sha256':hashlib.sha256(qemu.read_bytes()).hexdigest(),
        'config_sha256':hashlib.sha256((kernel.parents[3]/'.config').read_bytes()).hexdigest(),
        'initrd_sha256':hashlib.sha256(initrd.read_bytes()).hexdigest(),'trials':results}
(base/('summary-constrained.json' if args.constrained_only else 'summary.json')).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
