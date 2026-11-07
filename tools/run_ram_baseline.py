"""Fresh QEMU guests; synchronized RSS/PSS measurements (64 MiB by default)."""
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import statistics
import subprocess
import time
import argparse
import socket
import hashlib
import sys

base = Path(__file__).resolve().parent
lab = Path('/srv/cram-lab')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--resume', action='store_true')
parser.add_argument('--kernel', default=str(lab/'build-linux-apparmor-test/arch/x86/boot/bzImage'))
parser.add_argument('--reporting', choices=('absent','off','on'), default='absent')
parser.add_argument('--settle', type=float, default=0)
parser.add_argument('--repetitions', type=int, default=3)
parser.add_argument('--output', default='results')
parser.add_argument('--qemu', default=str(lab/'build-qemu/qemu-system-x86_64'))
parser.add_argument('--compressed-store', action='store_true')
parser.add_argument('--mib', type=int, default=64)
parser.add_argument('--patterns', nargs='+', choices=('repeat','mixed','random'), default=['repeat','mixed','random'])
parser.add_argument('--modes', nargs='+', choices=('dram','cram'), default=['dram','cram'])
parser.add_argument('--report-order', type=int, choices=range(11))
parser.add_argument('--reclaim-settle', type=float)
parser.add_argument('--exclusive', action='store_true')
parser.add_argument('--pcp-high-fraction', type=int, help='Guest-only per-CPU free-list bound (0 or >=8)')
parser.add_argument('--trace-source', action='store_true')
parser.add_argument('--disable-host-thp', action='store_true', help='Disable THP only for the QEMU child process')
parser.add_argument('--cycles', type=int, default=1, choices=range(1, 11))
parser.add_argument('--detailed-maps', action='store_true')
parser.add_argument('--allocator-diagnostics', action='store_true')
parser.add_argument('--trim-on-release', action='store_true')
parser.add_argument('--trim-stage',choices=('released','reclaimed'),default='released',help='Checkpoint for the optional explicit allocator-trim diagnostic')
parser.add_argument('--malloc-trim-threshold', type=int, help='Set glibc MALLOC_TRIM_THRESHOLD_ only for QEMU')
parser.add_argument('--balloon-test',type=int,choices=(1,4),help='Compare simulated trim (1) with real trim (4), including live data')
args = parser.parse_args()
if args.balloon_test and (args.modes != ['cram'] or not args.compressed_store):
    parser.error('--balloon-test requires --modes cram --compressed-store')
if args.trim_on_release and not args.allocator_diagnostics:
    parser.error('--trim-on-release requires --allocator-diagnostics')
if args.allocator_diagnostics and not args.compressed_store:
    parser.error('--allocator-diagnostics requires --compressed-store')
if args.pcp_high_fraction is not None and args.pcp_high_fraction != 0 and args.pcp_high_fraction < 8:
    parser.error('--pcp-high-fraction must be 0 or at least 8')
out = base / args.output
out.mkdir(exist_ok=args.resume)
if args.exclusive:
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if (proc/'comm').read_text().startswith('qemu-system'):
                raise RuntimeError('Another QEMU guest is running; exclusive measurement refused')
        except (FileNotFoundError, PermissionError):
            pass

def memory(pid):
    def fields(text):
        return {k: int(v) for k, v in re.findall(r'^(\w+):\s+(\d+) kB$', text, re.M)}
    rollup = fields(Path(f'/proc/{pid}/smaps_rollup').read_text())
    regions = []
    mappings = []
    for region in re.split(r'(?=^[0-9a-f]+-[0-9a-f]+ )', Path(f'/proc/{pid}/smaps').read_text(), flags=re.M):
        f = fields(region)
        if args.detailed_maps and f:
            header = region.splitlines()[0].split(maxsplit=5)
            label = header[5] if len(header) == 6 else 'anonymous'
            if label.startswith('/'): label = Path(label).name
            mappings.append({'address': header[0], 'permissions': header[1], 'label': label,
                             **{k: f.get(k, 0) for k in ('Size', 'Rss', 'Pss', 'Private_Dirty', 'Anonymous', 'Swap')}})
        if f.get('Size', 0) >= 256 * 1024:
            regions.append({k: f.get(k, 0) for k in ('Size', 'Rss', 'Pss', 'Anonymous', 'AnonHugePages', 'Swap')})
    result = {'rollup': rollup, 'large_regions': regions, 'timestamp_ns': time.time_ns()}
    if args.detailed_maps: result['mappings'] = mappings
    return result

environment = {
    'host_kernel': os.uname().release,
    'qemu_commit': subprocess.check_output(['git', '-C', str(lab/'qemu'), 'rev-parse', 'HEAD'], text=True).strip(),
    'ksm_run': Path('/sys/kernel/mm/ksm/run').read_text().strip(),
    'host_thp': Path('/sys/kernel/mm/transparent_hugepage/enabled').read_text().strip(),
    'host_meminfo': Path('/proc/meminfo').read_text(),
    'workload_bytes': args.mib * 1024 * 1024,
    'qemu_path': args.qemu, 'compressed_store': args.compressed_store,
    'qemu_sha256': hashlib.file_digest(open(args.qemu, 'rb'), 'sha256').hexdigest(),
    'kernel_sha256': hashlib.file_digest(open(args.kernel, 'rb'), 'sha256').hexdigest(),
    'kernel_path': args.kernel, 'free_page_reporting': args.reporting,
    'reporting_order': args.report_order,
    'pcp_high_fraction': args.pcp_high_fraction,
    'trace_source': args.trace_source,
    'qemu_thp_disabled': args.disable_host_thp,
    'cycles': args.cycles, 'detailed_maps': args.detailed_maps,
    'allocator_diagnostics': args.allocator_diagnostics, 'trim_on_release': args.trim_on_release,
    'trim_stage': args.trim_stage,
    'malloc_trim_threshold': args.malloc_trim_threshold,
    'balloon_test_trim_mode': args.balloon_test,
    'settle_seconds': args.settle, 'repetitions': args.repetitions,
    'reclaim_settle_seconds': args.reclaim_settle, 'exclusive': args.exclusive,
    'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
if not args.resume:
    (out/'environment.json').write_text(json.dumps(environment, indent=2)+'\n')
results = json.loads((out/'measurements.json').read_text()) if args.resume and (out/'measurements.json').exists() else []
for repeat in range(args.repetitions):
    for pattern in args.patterns:
        for mode in (args.modes if repeat % 2 == 0 else list(reversed(args.modes))):
            name = f'{repeat+1}-{pattern}-{mode}'
            if any(r['run'] == name for r in results): continue
            if (out/(name+'.txt')).exists():
                (out/(name+'.txt')).rename(out/(name+'-rejected-harness.txt'))
            command = shlex.split((lab/'run-cram-qemu-preflush.sh').read_text().split('exec ', 1)[1].replace('\\\n', ''))
            command[0] = args.qemu
            command[command.index('-initrd')+1] = str(base/'baseline.cpio.gz')
            command[command.index('-kernel')+1] = args.kernel
            if args.trace_source:
                command += ['-trace', f'enable=virtio_balloon_report,file={out / (name + "-report.trace")}']
            if args.reporting != 'absent':
                command += ['-device', 'virtio-balloon-pci,bus=pcie.0,free-page-reporting='+args.reporting]
            if args.compressed_store:
                for i, arg in enumerate(command):
                    if arg.startswith('cxl-ct3,'): command[i] += ',ct3-compress=on'
                qmp_path=out/'qmp.sock'
                if qmp_path.exists(): qmp_path.unlink()
                command += ['-qmp', f'unix:{qmp_path},server=on,wait=off']
            if args.disable_host_thp:
                command = [sys.executable, str(base/'exec_without_thp.py'), *command]
            child_env = os.environ.copy()
            if args.malloc_trim_threshold is not None:
                child_env['MALLOC_TRIM_THRESHOLD_'] = str(args.malloc_trim_threshold)
            p = subprocess.Popen(command, cwd=lab, env=child_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            sel = selectors.DefaultSelector(); sel.register(p.stdout, selectors.EVENT_READ)
            transcript = bytearray(); pending = b''; sent = False; checkpoints = {}; cycle = 0
            deadline = time.monotonic()+args.cycles*((900 if args.compressed_store else 120)+5*max(args.settle,args.reclaim_settle or 0))
            try:
                while time.monotonic() < deadline:
                    if not sel.select(1):
                        if p.poll() is not None: break
                        continue
                    chunk = os.read(p.stdout.fileno(), 65536)
                    if not chunk: break
                    transcript.extend(chunk); pending += chunk
                    if not sent and b'Interactive shell ready.' in transcript:
                        setup = '' if args.report_order is None else f'echo {args.report_order} > /sys/module/page_reporting/parameters/page_reporting_order && '
                        if args.pcp_high_fraction is not None:
                            setup += f'echo {args.pcp_high_fraction} > /proc/sys/vm/percpu_pagelist_high_fraction && '
                        if args.trace_source:
                            setup += 'export CRAM_TRACE_SOURCE=1 && '
                        workload = f'/bin/ram-savings-baseline {mode} {pattern} {args.mib}; echo BASELINE_EXIT=$?; '
                        activation = '/bin/activate-cram'
                        if args.balloon_test:
                            setup += 'export CRAM_BALLOON_TEST=1 && '
                            setup += f'''sed 's@echo online_movable@echo {args.balloon_test} > "$D/cram_trim"; echo online_movable@' /bin/activate-cram > /activate-balloon && '''
                            activation = 'sh /activate-balloon'
                        script = setup + activation + ' && { ' + workload * args.cycles + '}; ' + '''/bin/busybox awk 'END {print "GUEST_SWAP_ENTRIES=" NR-1}' /proc/swaps; echo STUDY_DONE; poweroff -f\n'''
                        p.stdin.write(script.encode()); p.stdin.flush(); sent = True
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        m = re.fullmatch(rb'CHECKPOINT=(\w+)\r?', line)
                        if m:
                            stage = m[1].decode()
                            if stage == 'idle': cycle += 1
                            key = stage if args.cycles == 1 else f'cycle{cycle}_{stage}'
                            if key in checkpoints: raise RuntimeError('duplicate checkpoint')
                            time.sleep(args.reclaim_settle if args.reclaim_settle is not None and stage in ('idle','placed','released','live_trimmed','live_restored','reclaimed','restored') else args.settle)
                            samples = []
                            for _ in range(3):
                                time.sleep(.15); samples.append(memory(p.pid))
                            checkpoints[key] = samples
                            if args.trace_source:
                                for sample in samples:
                                    sample['report_trace_bytes'] = (out / (name + '-report.trace')).stat().st_size
                            if args.compressed_store:
                                with socket.socket(socket.AF_UNIX) as sock:
                                    sock.settimeout(5); sock.connect(str(qmp_path))
                                    stream=sock.makefile('rwb', buffering=0)
                                    json.loads(stream.readline())
                                    def query(execute, arguments=None):
                                        request={'execute':execute}
                                        if arguments: request['arguments']=arguments
                                        stream.write((json.dumps(request)+'\n').encode())
                                        while True:
                                            reply=json.loads(stream.readline())
                                            if 'error' in reply: raise RuntimeError(reply)
                                            if 'return' in reply: return reply['return']
                                    query('qmp_capabilities')
                                    stats={key:query('qom-get',{'path':'/machine/peripheral/ct3','property':key})
                                           for key in ('store-payload-bytes','store-cache-misses','store-read-ops','store-write-ops')}
                                    for sample in samples: sample['store']=stats
                                    if args.allocator_diagnostics:
                                        def allocator_stats():
                                            raw = query('qom-get', {'path':'/machine/peripheral/ct3', 'property':'lab-allocator-stats'})
                                            return {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', raw)}
                                        allocation = allocator_stats()
                                        for sample in samples: sample['allocator'] = allocation
                                        if args.trim_on_release and stage == args.trim_stage:
                                            query('qom-set', {'path':'/machine/peripheral/ct3', 'property':'lab-allocator-trim', 'value':True})
                                            time.sleep(1)
                                            trimmed = [memory(p.pid) for _ in range(3)]
                                            after_allocation = allocator_stats()
                                            for sample in samples:
                                                sample['after_trim'] = trimmed
                                                sample['allocator_after_trim'] = after_allocation
                                    stream.close()
                            p.stdin.write(b'GO\n'); p.stdin.flush()
                            print(f'{name} {key}: PSS={statistics.median(x["rollup"]["Pss"] for x in samples)/1024:.3f} MiB', flush=True)
                p.wait(timeout=5)
                text = transcript.decode(errors='replace').replace('\r', '')
                text = re.sub(r'\([^\s()]+@[^\s()]+\)', '(builder@lab)', text)
                (out/(name+'.txt')).write_text(text)
                if p.returncode or re.findall(r'^BASELINE_EXIT=(\d+)$', text, re.M) != ['0']*args.cycles: raise RuntimeError(name+' failed')
                stages = {'idle', 'filled', 'placed', 'verified', 'released'}
                if args.balloon_test:
                    stages |= {'live_trimmed','live_restored','reclaimed','restored'}
                    assert re.findall(r'^LIVE_INTEGRITY=(\w+) PASS$',text,re.M)==['inflated','restored']*args.cycles
                expected = stages if args.cycles == 1 else {f'cycle{c}_{s}' for c in range(1,args.cycles+1) for s in stages}
                if set(checkpoints) != expected: raise RuntimeError('missing checkpoint')
                if len(re.findall(r'^INTEGRITY=PASS$', text, re.M)) != args.cycles or not re.search(r'^STUDY_DONE$', text, re.M): raise RuntimeError('missing integrity/completion')
                if not re.search(r'^GUEST_SWAP_ENTRIES=0$', text, re.M): raise RuntimeError('guest swap active or malformed')
                if any(x['rollup'].get('Swap', 0) for samples in checkpoints.values() for x in samples): raise RuntimeError('host swapped QEMU')
                if args.disable_host_thp and any(x['rollup'].get('AnonHugePages', 0) for samples in checkpoints.values() for x in samples):
                    raise RuntimeError('QEMU has transparent huge pages despite requested disable')
                row = {'run': name, 'pattern': pattern, 'mode': mode, 'repeat': repeat+1, 'checkpoints': checkpoints,
                       'cycle_placed_pages': [int(x) for x in re.findall(r'^PLACED_PAGES=(\d+)$', text, re.M)],
                       'cycle_read_verify_seconds': [float(x) for x in re.findall(r'^READ_VERIFY_SECONDS=([\d.]+)$', text, re.M)],
                       'placed_pages': int(re.search(r'^PLACED_PAGES=(\d+)$', text, re.M)[1]),
                       'read_verify_seconds': float(re.search(r'^READ_VERIFY_SECONDS=([\d.]+)$', text, re.M)[1]),
                       'clock_warning': 'clocksource: Watchdog' in text}
                results.append(row)
                (out/'measurements.json').write_text(json.dumps(results, indent=2)+'\n')
                print(name+' PASS', flush=True)
            finally:
                sel.close()
                if p.poll() is None:
                    p.terminate()
                    try: p.wait(timeout=5)
                    except subprocess.TimeoutExpired: p.kill(); p.wait()
print('BASELINE_RUNS_PASS='+str(len(results)))
