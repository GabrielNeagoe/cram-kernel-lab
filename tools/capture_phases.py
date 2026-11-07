"""Run on the Ubuntu host via sudo; --dry-run needs no privilege or tracing."""
import argparse
import datetime
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import shlex
import subprocess
import time
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dry-run', action='store_true')
args = parser.parse_args()
base = Path(__file__).resolve().parent
lab = Path('/srv/cram-lab')
owner = pwd.getpwnam(os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name)
if not args.dry_run and (os.geteuid() != 0 or owner.pw_uid == 0):
    parser.error('Use sudo from the regular lab account for a traced capture')
name = 'cram-phases-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
out = base / 'captures' / name
out.mkdir(parents=True)
print('CAPTURE_DIR=' + str(out), flush=True)
instance = None
p = None
selector = selectors.DefaultSelector()
manifest = dict(dry_run=args.dry_run, phases=[], guest_exits=[], integrity=[], demotions=[], completed=False)
active = None
started = False
serial = (out/'serial.txt').open('w', buffering=1)

def trace_write(file, value):
    (instance/file).write_text(str(value))

def send(line):
    p.stdin.write(line.encode()); p.stdin.flush()

def line_received(line):
    global active, started
    line = re.sub(r'^(?:~ # )+', '', line)
    if line == 'LAB_READY':
        if started: raise RuntimeError('Duplicate ready marker')
        started = True
        tids = sorted(int(x.name) for x in Path(f'/proc/{p.pid}/task').iterdir())
        manifest['qemu_pid'] = p.pid; manifest['qemu_tids'] = tids
        if instance:
            trace_write('set_event_pid', ' '.join(map(str, tids + [os.getpid()])))
            trace_write('tracing_on', '1')
            trace_write('trace_marker', 'CRAM_CAPTURE_BEGIN')
        send('for r in 1 2 3; do for mode in direct preflush; do /bin/cram-bench-phased "$r-$mode" "$mode"; echo RUN_EXIT=$?; done; done; echo LAB_DONE; poweroff -f\n')
    m = re.fullmatch(r'PHASE_(READY|DONE),([a-z0-9_.-]+)', line)
    if m:
        action, phase = m.groups()
        if action == 'READY':
            if active is not None: raise RuntimeError('Overlapping phase')
            active = phase
            if instance: trace_write('trace_marker', 'CRAM_BEGIN ' + phase)
        else:
            if active != phase: raise RuntimeError('Mismatched phase')
            if instance: trace_write('trace_marker', 'CRAM_END ' + phase)
            manifest['phases'].append(phase); active = None
        send('\n')
    if line.startswith('RUN_EXIT='): manifest['guest_exits'].append(int(line.split('=')[1]))
    if line.startswith('INTEGRITY,'): manifest['integrity'].append(line)
    if line.startswith('DEMOTED,'): manifest['demotions'].append(line)
    if line == 'LAB_DONE':
        manifest['completed'] = True
        if instance:
            trace_write('trace_marker', 'CRAM_CAPTURE_END'); trace_write('tracing_on', '0')

try:
    if not args.dry_run:
        instance = Path('/sys/kernel/tracing/instances') / name
        instance.mkdir()
        trace_write('tracing_on', '0')
        trace_write('buffer_size_kb', '4096')
        trace_write('trace_clock', 'mono')
        trace_write('events/kvm/kvm_page_fault/enable', '1')
        (out/'event-format.txt').write_text((instance/'events/kvm/kvm_page_fault/format').read_text())
    launcher = (lab/'run-cram-qemu-preflush.sh').read_text()
    command = shlex.split(launcher.split('exec ', 1)[1].replace('\\\n', ''))
    command[command.index('-initrd') + 1] = str(base/'phased.cpio.gz')
    manifest['command'] = command
    child_env = dict(os.environ, HOME=owner.pw_dir, USER=owner.pw_name, LOGNAME=owner.pw_name)
    child_options = dict(user=owner.pw_uid, group=owner.pw_gid, extra_groups=os.getgrouplist(owner.pw_name, owner.pw_gid)) if os.geteuid() == 0 else {}
    p = subprocess.Popen(command, cwd=lab, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, bufsize=0, env=child_env, **child_options)
    selector.register(p.stdout, selectors.EVENT_READ)
    pending = b''; initialized = False
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if not selector.select(1):
            if p.poll() is not None: break
            continue
        chunk = os.read(p.stdout.fileno(), 65536)
        if not chunk: break
        decoded = chunk.decode('utf-8', errors='replace')
        serial.write(decoded)
        pending += chunk
        if not initialized and b'Interactive shell ready.' in pending:
            initialized = True
            send('/bin/busybox stty -echo; /bin/activate-cram && { echo REGION_START; cat /sys/bus/cxl/devices/region0/resource; echo REGION_SIZE; cat /sys/bus/cxl/devices/region0/size; echo LAB_READY; }\n')
        while b'\n' in pending:
            raw, pending = pending.split(b'\n', 1)
            line_received(raw.decode('utf-8', errors='replace').strip('\r'))
    p.wait(timeout=5)
    manifest['qemu_exit'] = p.returncode
    if not manifest['completed'] or active or p.returncode or len(manifest['guest_exits']) != 6 or any(x not in (0,17) for x in manifest['guest_exits']):
        raise RuntimeError('Guest capture did not finish successfully')
    successful = manifest['guest_exits'].count(0)
    expected_phases = sum((6 if i % 2 == 0 else 7) if rc == 0 else 3 for i, rc in enumerate(manifest['guest_exits']))
    if len(manifest['phases']) != expected_phases or len(manifest['integrity']) != successful or any(not x.endswith(',PASS') for x in manifest['integrity']):
        raise RuntimeError('Incomplete phase or integrity results')
    if len(manifest['demotions']) != 6 or any(not value.endswith(',8192') for value, rc in zip(manifest['demotions'], manifest['guest_exits']) if rc == 0):
        raise RuntimeError('Unexpected demotion counts')
    manifest['validation'] = 'PASS' if successful == 6 else 'PARTIAL_DEMOTION'
except Exception as e:
    manifest['error'] = str(e)
    raise
finally:
    if p is not None and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    selector.close(); serial.close()
    if instance:
        trace_write('tracing_on', '0')
        (out/'trace.txt').write_text((instance/'trace').read_text())
        stats = {str(f.relative_to(instance)): f.read_text() for f in instance.glob('per_cpu/cpu*/stats')}
        (out/'trace-stats.json').write_text(json.dumps(stats, indent=2)+'\n')
        loss = sum(int(n) for data in stats.values() for n in re.findall(r'^(?:overrun|commit overrun|dropped events):\s*(\d+)', data, re.M))
        manifest['trace_loss_counters'] = loss
        manifest['trace_integrity'] = 'PASS' if loss == 0 else 'LOST_EVENTS'
        trace_write('events/kvm/kvm_page_fault/enable', '0')
        instance.rmdir()
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    if os.geteuid() == 0:
        for f in [out.parent, out, *out.iterdir()]: os.chown(f, owner.pw_uid, owner.pw_gid)
    print('CAPTURE_STATUS=' + manifest.get('validation', 'FAIL'), flush=True)
