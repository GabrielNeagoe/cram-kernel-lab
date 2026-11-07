"""Supplemental arena checks: stored-data fork/COW with stores disabled, and invalid initialization."""
from pathlib import Path
import base64,argparse,subprocess,tempfile,selectors,os,time,json,re,hashlib,gzip,sys
p=argparse.ArgumentParser();p.add_argument('--case',choices=['fork','invalid-init','exit'],required=True);a=p.parse_args()
b=Path(__file__).resolve().parent;lab=Path('/srv/cram-lab');out=b/a.case;out.mkdir(exist_ok=False)
assert not any(p.read_text().startswith('qemu-system') for p in Path('/proc').glob('[0-9]*/comm') if p.exists())
over=b/'supplemental-overlay';(over/'bin').mkdir(parents=True,exist_ok=True)
subprocess.run(['gcc','-static','-O2','-Wall','-Wextra','-Werror',str(b/('reservation-exit.c' if a.case=='exit' else 'arena-fork.c')),'-o',str(over/'bin/arena-fork')],check=True)
paths=subprocess.check_output(['find','.','-print0'],cwd=over);cpio=subprocess.run(['cpio','--null','-o','-H','newc'],cwd=over,input=paths,stdout=subprocess.PIPE,check=True).stdout
initrd=out/'supplemental.cpio.gz';initrd.write_bytes((lab/'images/initramfs-cram-preflush.cpio.gz').read_bytes()+gzip.compress(cpio))
kernel=lab/'build-linux-cram-arena/arch/x86/boot/bzImage';qemu=lab/'build-qemu-compressed/qemu-system-x86_64'
mb=3 if a.case=='invalid-init' else 4
with tempfile.TemporaryDirectory(dir=b,prefix='scratch-supplemental-') as tmp:
 disk=Path(tmp)/'scratch.raw'
 with disk.open('xb') as f:f.truncate(128*1024*1024)
 cmd=[sys.executable,str(b/'exec_without_thp.py'),str(qemu),'-L',str(lab/'qemu/pc-bios'),'-machine','q35','-accel','tcg','-cpu','max','-smp','2','-m','768M','-kernel',str(kernel),'-initrd',str(initrd),'-append',f'console=ttyS0 rdinit=/init panic=-1 zswap.enabled=1 zswap.cram_arena_mb={mb}','-drive',f'file={disk},format=raw,if=virtio,cache=none','-nographic','-monitor','none','-nic','none','-no-reboot']
 if a.case=='fork':
  script='mount -t debugfs debugfs /sys/kernel/debug; mkdir -p /sys/fs/cgroup; mount -t cgroup2 none /sys/fs/cgroup; echo +memory > /sys/fs/cgroup/cgroup.subtree_control; mkdir /sys/fs/cgroup/target; echo 3145728 > /sys/fs/cgroup/target/memory.zswap.max; echo 67108864 > /sys/fs/cgroup/target/memory.max; echo 67108864 > /sys/fs/cgroup/target/memory.swap.max; echo $$ > /sys/fs/cgroup/target/cgroup.procs; /bin/busybox mkswap /dev/vda && /bin/busybox swapon /dev/vda && { for pattern in repeat mixed random; do /bin/arena-fork "$pattern"; echo TEST_EXIT=$?; done; }; echo RESERVED_END=$(cat /sys/fs/cgroup/target/memory.cram.reserved); poweroff -f\n'
 elif a.case=='exit':
  script='mount -t debugfs debugfs /sys/kernel/debug; mkdir -p /sys/fs/cgroup; mount -t cgroup2 none /sys/fs/cgroup; echo +memory > /sys/fs/cgroup/cgroup.subtree_control; mkdir /sys/fs/cgroup/budget /sys/fs/cgroup/neighbor; echo +memory > /sys/fs/cgroup/budget/cgroup.subtree_control; mkdir /sys/fs/cgroup/budget/target; echo 67108864 > /sys/fs/cgroup/budget/memory.zswap.max; echo 67108864 > /sys/fs/cgroup/budget/target/memory.max; echo 67108864 > /sys/fs/cgroup/budget/target/memory.swap.max; echo 3145728 > /sys/fs/cgroup/budget/target/memory.zswap.max; echo 1 > /sys/fs/cgroup/budget/target/memory.oom.group; /bin/busybox mkswap /dev/vda && /bin/busybox swapon /dev/vda && /bin/arena-fork kill 1; echo TEST_EXIT=$?; poweroff -f\n'
 else:
  script='mount -t debugfs debugfs /sys/kernel/debug; echo ENABLED=$(cat /sys/module/zswap/parameters/enabled); test ! -e /sys/kernel/debug/zswap/arena_backing_bytes && echo INVALID_ARENA_REJECT_PASS; poweroff -f\n'
 payload=base64.b64encode(script.encode()).decode();chunks=[payload[i:i+192] for i in range(0,len(payload),192)];upload_index=-1;upload_deadline=0
 (out/'guest-script.sh').write_text(script)
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0);sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ);data=bytearray();sent=False;deadline=time.monotonic()+180
 try:
  while time.monotonic()<deadline:
   if sent and upload_index<=len(chunks) and time.monotonic()>upload_deadline:raise RuntimeError('Guest script upload timed out')
   if not sel.select(1):
    if proc.poll() is not None:break
    continue
   chunk=os.read(proc.stdout.fileno(),65536)
   if not chunk:break
   data.extend(chunk)
   if re.search(rb'BUG:|Oops:|Kernel panic|WARNING:',data[-32768:]):raise RuntimeError('kernel fault')
   if not sent and b'Interactive shell ready.' in data:
    proc.stdin.write(b'PS1=; /bin/busybox stty -echo && echo CRAM_SERIAL_READY\n');proc.stdin.flush();sent=True;upload_deadline=time.monotonic()+30
   if sent and upload_index<=len(chunks):
    clean=re.sub(rb'\[\s*\d+\.\d+\] [^\n]*\n',b'',bytes(data[-32768:])).replace(b'\r',b'')
    marker=b'CRAM_SERIAL_READY' if upload_index<0 else f'CRAM_UPLOAD_ACK_{upload_index}'.encode()
    if re.search(rb'(?:^|\n)(?:~ # |/ # )?'+marker+rb'\n',clean):
     upload_index+=1
     if upload_index<len(chunks):
      mode='>' if upload_index==0 else '>>';prefix='/bin/busybox dmesg -n 5; ' if upload_index==0 else ''
      line=f"{prefix}printf '%s' '{chunks[upload_index]}' {mode} /tmp/cram-launch.b64; echo CRAM_UPLOAD_ACK_{upload_index}\n"
     else:
      line='/bin/busybox base64 -d /tmp/cram-launch.b64 > /tmp/cram-launch.sh && /bin/sh /tmp/cram-launch.sh\n';upload_index+=1
     proc.stdin.write(line.encode());proc.stdin.flush();upload_deadline=time.monotonic()+30
  proc.wait(timeout=5);assert proc.returncode==0
 finally:
  if proc.poll() is None:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  sel.close();log=re.sub(r'\([^\s()]+@[^\s()]+\)','(builder@lab)',data.decode(errors='replace').replace('\r',''));(out/'guest.txt').write_text(log)
result={'case':a.case,'kernel_sha256':hashlib.sha256(kernel.read_bytes()).hexdigest(),'qemu_sha256':hashlib.sha256(qemu.read_bytes()).hexdigest(),'initrd_sha256':hashlib.sha256(initrd.read_bytes()).hexdigest(),'workload_sha256':hashlib.sha256((over/'bin/arena-fork').read_bytes()).hexdigest()}
if a.case=='fork':
 assert re.findall(r'^TEST_EXIT=(\d+)$',log,re.M)==['0']*3
 assert re.findall(r'^PATTERN=(\w+) INTEGRITY=PASS FORK_COW=PASS$',log,re.M)==['repeat','mixed','random']
 assert re.search(r'^RESERVED_END=0$',log,re.M)
 compressed=[tuple(map(int,v)) for v in re.findall(r'^SNAPSHOT=compressed swapped=(\d+) stored=(\d+) pool_bytes=(\d+)$',log,re.M)]
 assert len(compressed)==3 and all(v[0]==4096 and v[2]==4*2**20 for v in compressed)
 assert compressed[0][1]>0 and compressed[1][1]>0 and compressed[2][1]==0
 assert all(int(v)==0 for v in re.findall(r'^SNAPSHOT=released swapped=0 stored=(\d+)',log,re.M))
 result['compressed_snapshots']=compressed
elif a.case=='exit':
 assert re.search(r'^KILL_CLEANUP_PASS$',log,re.M) and re.search(r'^TEST_EXIT=0$',log,re.M)
else:
 assert re.search(r'^ENABLED=N$',log,re.M) and re.search(r'^INVALID_ARENA_REJECT_PASS$',log,re.M)
result['result']='PASS';(out/'measurements.json').write_text(json.dumps(result,indent=2)+'\n');print(a.case+' PASS')
