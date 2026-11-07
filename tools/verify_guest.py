"""Run on the Ubuntu lab host. Starts and stops only its own temporary QEMU."""
import os
import selectors
import subprocess
import sys
import time
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--recovery', action='store_true', help='Observe recovery and refresh VM statistics in a fresh guest')
args = parser.parse_args()

os.chdir('/srv/cram-lab')
commands = r'''
echo VERIFY_BEGIN
uname -r
/bin/activate-cram
echo ACTIVATE_EXIT=$?
echo REGION_START; cat /sys/bus/cxl/devices/region0/resource
echo REGION_SIZE; cat /sys/bus/cxl/devices/region0/size
echo CLOCKSOURCE; cat /sys/devices/system/clocksource/clocksource0/current_clocksource
if [ "$(cat /sys/bus/dax/devices/dax0.0/cram)" = 1 ]; then for n in 1 2 3 4 5; do echo BENCH_RUN=$n; /bin/cram-bench-preflush; echo BENCH_EXIT=$?; done; fi
sleep 30
echo FINAL_NODES; cat /sys/kernel/debug/cram/nodes
echo FINAL_DEMOTIONS; cat /sys/kernel/debug/cram/demote_count
echo FINAL_PROMOTE_FAIL; cat /sys/kernel/debug/cram/promote_fail
echo VERIFY_END
poweroff -f
'''
if args.recovery:
    commands = r'''
echo RECOVERY_BEGIN
uname -r
/bin/activate-cram
echo ACTIVATE_EXIT=$?
echo BASELINE; cat /sys/kernel/debug/cram/nodes
for n in 1 2 3 4 5; do echo BENCH_RUN=$n; /bin/cram-bench-preflush; echo BENCH_EXIT=$?; done
for elapsed in 0 30 60 90 120; do echo RECOVERY_SECONDS=$elapsed; cat /proc/uptime; cat /sys/kernel/debug/cram/nodes; cat /sys/devices/system/node/node2/meminfo; [ "$elapsed" = 120 ] || sleep 30; done
echo BEFORE_STAT_REFRESH; cat /sys/kernel/debug/cram/nodes
cat /proc/sys/vm/stat_refresh
echo REFRESH_EXIT=$?
echo AFTER_STAT_REFRESH; cat /sys/kernel/debug/cram/nodes
cat /sys/devices/system/node/node2/meminfo
cat /proc/buddyinfo
echo FINAL_DEMOTIONS; cat /sys/kernel/debug/cram/demote_count
echo FINAL_PROMOTE_FAIL; cat /sys/kernel/debug/cram/promote_fail
echo RECOVERY_END
poweroff -f
'''
p = subprocess.Popen(['bash','run-cram-qemu-preflush.sh'], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
selector = selectors.DefaultSelector()
selector.register(p.stdout, selectors.EVENT_READ)
deadline = time.monotonic() + (240 if args.recovery else 150)
tail = b''
sent = False
try:
    while time.monotonic() < deadline:
        ready = selector.select(1)
        if ready:
            chunk = os.read(p.stdout.fileno(), 65536)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()
            tail = (tail + chunk)[-8192:]
            if not sent and b'Interactive shell ready.' in tail:
                p.stdin.write(commands.encode()); p.stdin.flush(); sent = True
        elif p.poll() is not None:
            break
    if p.poll() is None:
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise TimeoutError('Temporary verification guest did not exit')
    print('QEMU_EXIT=' + str(p.returncode))
    if not sent or p.returncode:
        raise RuntimeError('Guest launch failed')
finally:
    selector.close()
    if p.poll() is None:
        p.terminate()
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
