"""Run on the Ubuntu lab host beside placement.cpio.gz; no sudo needed."""
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys
import time
import argparse
import re
import base64
import textwrap

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--access-state',action='store_true')
parser.add_argument('--selftests',action='store_true')
parser.add_argument('--relocation',action='store_true')
parser.add_argument('--smoke',action='store_true')
parser.add_argument('--apparmor',action='store_true')
parser.add_argument('--without-kunit',action='store_true',help='For distribution kernel baselines without built-in KUnit tests')
parser.add_argument('--kernel')
parser.add_argument('--initrd')
parser.add_argument('--qemu')
parser.add_argument('--compressed-store',action='store_true')
parser.add_argument('--free-page-reporting',action='store_true')
parser.add_argument('--rounds',type=int,default=3)
parser.add_argument('--lab-trim',action='store_true',help='Opt in to experimental kernel trim mode 4 and exercise balloon reuse')
parser.add_argument('--lab-trim-reject',action='store_true',help='Negative test: mismatch VM identity and require zero successful trims')
args=parser.parse_args()

base=Path(__file__).resolve().parent
lab=Path('/srv/cram-lab')
command=shlex.split((lab/'run-cram-qemu-preflush.sh').read_text().split('exec ',1)[1].replace('\\\n',''))
if args.qemu:command[0]=args.qemu
if args.compressed_store:
    for i,arg in enumerate(command):
        if arg.startswith('cxl-ct3,'):command[i]+=',ct3-compress=on'
if args.free_page_reporting:
    command += ['-device','virtio-balloon-pci,bus=pcie.0,free-page-reporting=on']
    command[command.index('-append')+1]+=' page_reporting.page_reporting_order=0'
command[command.index('-initrd')+1]=args.initrd or str(base/'placement.cpio.gz')
if args.lab_trim_reject:
    assert args.lab_trim
    command += ['-smbios','type=1,manufacturer=CRAMNegative']
if args.kernel:command[command.index('-kernel')+1]=args.kernel
p=subprocess.Popen(command,cwd=lab,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
commands='''/bin/activate-cram && { for r in 1 2 3 4 5; do for cpu in -1 0; do /bin/cram-placement-probe "place-$r-$cpu" placement "$cpu"; echo PROBE_EXIT=$?; done; done; for r in 1 2 3; do for cpu in 0 1; do /bin/cram-placement-probe "cpu-$r-$cpu" cpu "$cpu"; echo PROBE_EXIT=$?; done; done; }; echo STUDY_DONE; poweroff -f
'''
if args.access_state:
    commands='''/bin/activate-cram && { for r in 1 2 3 4 5; do /bin/cram-access-state-probe "access-$r"; echo PROBE_EXIT=$?; done; }; echo STUDY_DONE; poweroff -f
'''
if args.selftests:
    commands='''/bin/activate-cram && { for round in 1 2 3; do echo ROUND=$round; echo TEST=readable; /bin/busybox timeout 30 /bin/busybox taskset -c 0 /bin/cram_readable_tool 2; echo TEST_EXIT=$?; echo TEST=fork32; /bin/busybox timeout 30 /bin/busybox taskset -c 0 /bin/cram_fork_tool 2 32; echo TEST_EXIT=$?; for sub in mprotect dontneed mremap madvfree mlock exit thp; do echo TEST=$sub; /bin/busybox timeout 30 /bin/busybox taskset -c 0 /bin/cram_ops_tool 2 "$sub"; echo TEST_EXIT=$?; done; done; for elapsed in 0 30 60 90; do echo RECOVERY_SECONDS=$elapsed; cat /sys/kernel/debug/cram/nodes; [ "$elapsed" = 90 ] || sleep 30; done; echo FINAL_PROMOTE_FAIL; cat /sys/kernel/debug/cram/promote_fail; }; echo STUDY_DONE; poweroff -f
'''
if args.relocation:
    commands='''/bin/activate-cram && { for r in 1 2 3 4 5; do echo TEST=forced-relocation; /bin/busybox timeout 30 /bin/busybox taskset -c 0 /bin/cram_ops_relocate_tool 2 mremap; echo TEST_EXIT=$?; done; }; echo STUDY_DONE; poweroff -f
'''
if args.smoke:
    commands='''echo CANDIDATE_RELEASE; uname -r; test -d /sys/bus/pci/drivers/mptspi && grep -qw ext4 /proc/filesystems && grep -q cram-vm /proc/sys/kernel/osrelease; echo SMOKE_EXIT=$?; echo CANDIDATE_DRIVERS; ls /sys/bus/pci/drivers/mptspi; cat /proc/filesystems; echo STUDY_DONE; poweroff -f
'''
if args.apparmor:
    commands='/aa-check.sh; echo APPARMOR_EXIT=$?; echo STUDY_DONE; poweroff -f\n'
if args.selftests:
    assert 1 <= args.rounds <= 3
    commands=commands.replace('for round in 1 2 3;', 'for round in '+' '.join(str(i+1) for i in range(args.rounds))+';')
if args.lab_trim:
    assert args.selftests and args.compressed_store
    activation = '''sed 's@echo online_movable@echo 4 > "$D/cram_trim"; echo online_movable@' /bin/activate-cram > /activate-lab; sh /activate-lab'''
    cycle = '''D=/sys/bus/dax/devices/dax0.0; C=/sys/kernel/debug/cram; echo 2990 > $D/cram_compression_ratio; for n in $(seq 1 30); do awk '$1==2 && $5==$6 && $5>0 {ok=1} END {exit !ok}' $C/nodes && break; sleep 1; done; echo LAB_TRIM_INFLATED; cat $C/nodes; echo LAB_TRIM_COUNT=$(cat $D/cram_trim_count); echo 3000 > $D/cram_compression_ratio; for n in $(seq 1 30); do awk '$1==2 && $5==0 && $6==0 {ok=1} END {exit !ok}' $C/nodes && break; sleep 1; done; echo LAB_TRIM_DEFLATED; cat $C/nodes;'''
    commands=commands.replace('/bin/activate-cram &&', activation+' &&')
    commands=commands.replace('{ for round', '{ '+cycle+' for round')
    commands=commands.replace('for elapsed in 0 30 60 90;', cycle+' for elapsed in 0 30 60 90;')
sent=False;tail=b'';transcript=bytearray();deadline=time.monotonic()+(1000 if args.selftests else 180)
try:
    while time.monotonic()<deadline:
        if not sel.select(1):
            if p.poll() is not None:break
            continue
        chunk=os.read(p.stdout.fileno(),65536)
        if not chunk:break
        sys.stdout.buffer.write(chunk);sys.stdout.buffer.flush();tail=(tail+chunk)[-8192:]
        if args.selftests or args.relocation or args.smoke or args.apparmor:transcript.extend(chunk)
        if not sent and b'Interactive shell ready.' in tail:
            if args.lab_trim:
                encoded='\n'.join(textwrap.wrap(base64.b64encode(commands.encode()).decode(),76))
                transport="/bin/busybox base64 -d > /run-study.sh <<'CRAM_DATA'\n"+encoded+"\nCRAM_DATA\nsh /run-study.sh\n"
                for line in transport.splitlines(True):
                    p.stdin.write(line.encode());p.stdin.flush();time.sleep(0.02)
            else:
                p.stdin.write(commands.encode());p.stdin.flush()
            sent=True
    p.wait(timeout=5)
    print('QEMU_EXIT='+str(p.returncode))
    if p.returncode or b'STUDY_DONE\r\n' not in tail:raise RuntimeError('Study did not complete')
    if args.smoke and not re.search(rb'^SMOKE_EXIT=0\r?$', transcript, re.M):
        raise RuntimeError('Candidate smoke check failed')
    if args.apparmor:
        if not re.search(rb'^APPARMOR_EXIT=0\r?$', transcript, re.M):
            raise RuntimeError('AppArmor functional checks failed')
        if re.search(rb'not ok \d+ - apparmor|Kernel panic|BUG:|Oops:', transcript):
            raise RuntimeError('Kernel test failure or crash')
        if not args.without_kunit and not re.search(rb'ok \d+ apparmor_policy_unpack', transcript):
            raise RuntimeError('AppArmor KUnit suite pass marker missing')
    if args.selftests or args.relocation:
        codes=re.findall(rb'^TEST_EXIT=(\d+)\r?$',transcript,re.M)
        expected=5 if args.relocation else 9*args.rounds
        if len(codes)!=expected or any(code!=b'0' for code in codes):
            raise RuntimeError(f'Expected {expected} successful helper tests; observed {codes!r}')
        print(f'HELPER_TESTS_PASS={expected}')
        if args.lab_trim:
            counts=[int(n) for n in re.findall(rb'^LAB_TRIM_COUNT=(\d+)\r?$',transcript,re.M)]
            assert len(counts)==2, counts
            if args.lab_trim_reject:
                assert counts == [0,0], counts
            else:
                assert counts[0]>0 and counts[1]>counts[0], counts
            for marker, inflated in ((b'LAB_TRIM_INFLATED',True),(b'LAB_TRIM_DEFLATED',False)):
                states=re.findall(rb'^'+marker+rb'\r?\nnode[^\n]*\n(2 [0-9 ]+)\r?$',transcript,re.M)
                assert len(states)==2, states
                for line in states:
                    fields=[int(x) for x in line.split()]
                    assert fields[4]==fields[5] and (fields[4]>0 if inflated else fields[4]==0), fields
            print('LAB_TRIM_CYCLES_PASS=2')
finally:
    sel.close()
    if p.poll() is None:
        p.terminate()
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.wait()
