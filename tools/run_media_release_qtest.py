"""Exercise actual QEMU mailbox MMIO with an offline CXL range in a lab guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import selectors
import shlex
import socket
import struct
import subprocess
import tempfile
import time

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--output', type=Path, required=True)
args = ap.parse_args()
lab = Path('/srv/cram-lab')
qemu = lab/'build-qemu-compressed/qemu-system-x86_64'
kernel = lab/'build-linux-reporting/arch/x86/boot/bzImage'
command = shlex.split((lab/'run-cram-qemu-preflush.sh').read_text().split('exec ',1)[1].replace('\\\n',''))
command[0] = str(qemu)
command[command.index('-kernel')+1] = str(kernel)
for i, arg in enumerate(command):
    if arg.startswith('cxl-ct3,'):
        command[i] += ',ct3-compress=on'
setup = '''S=/sys/bus/cxl/devices; R=$S/region0; E=$S/decoder2.0
echo region0 > $S/decoder0.0/create_ram_region && echo ram > $E/mode && echo 0x10000000 > $E/dpa_size && echo 1 > $R/interleave_ways && echo 4096 > $R/interleave_granularity && echo 0x10000000 > $R/size && echo decoder2.0 > $R/target0 && echo 1 > $R/commit && test ! -d /sys/devices/system/node/node2 && echo REGION_OFFLINE && echo REGION_ADDRESS=$(cat $R/resource) && echo REGION_COMMITTED
'''
args.output.mkdir(parents=True, exist_ok=True)
transcript = bytearray()
results = {'qemu_sha256':hashlib.sha256(qemu.read_bytes()).hexdigest(),
           'kernel_sha256':hashlib.sha256(kernel.read_bytes()).hexdigest(),
           'scope':'Real QEMU MMIO; CXL region committed with node2 absent; CPUs paused during test',
           'checks':[]}
with tempfile.TemporaryDirectory(prefix='cram-mailbox-') as tmp:
    command += ['-qmp', f'unix:{tmp}/qmp,server=on,wait=off',
                '-qtest', f'unix:{tmp}/qtest,server=on,wait=off', '-qtest-log', '/dev/null']
    proc = subprocess.Popen(command,cwd=lab,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,bufsize=0)
    sel = selectors.DefaultSelector(); sel.register(proc.stdout,selectors.EVENT_READ)
    try:
        sent=False; deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if sel.select(1):
                data=os.read(proc.stdout.fileno(),65536)
                if not data: raise RuntimeError('Guest exited before setup')
                transcript.extend(data)
                if not sent and b'Interactive shell ready.' in transcript:
                    proc.stdin.write(setup.encode()); proc.stdin.flush(); sent=True
                if re.search(rb'^REGION_COMMITTED\r?$',transcript,re.M): break
        else: raise TimeoutError('Guest region setup timed out')
        match=re.search(rb'^REGION_ADDRESS=(0x[0-9a-fA-F]+)\r?$',transcript,re.M)
        assert match, 'Missing region address'
        assert re.search(rb'^REGION_OFFLINE\r?$',transcript,re.M), 'Tier must not be online'
        address=int(match[1],16)
        qs=socket.socket(socket.AF_UNIX); qs.settimeout(15); qs.connect(tmp+'/qmp')
        qf=qs.makefile('rwb',buffering=0); json.loads(qf.readline())
        def qmp(name, arguments=None):
            qf.write((json.dumps({'execute':name,'arguments':arguments or {}})+'\n').encode())
            while True:
                reply=json.loads(qf.readline())
                if 'error' in reply: raise RuntimeError(reply)
                if 'return' in reply: return reply['return']
        qmp('qmp_capabilities'); qmp('stop')
        def find_device(obj):
            if isinstance(obj,dict):
                if obj.get('qdev_id')=='ct3': return obj
                for value in obj.values():
                    found=find_device(value)
                    if found: return found
            elif isinstance(obj,list):
                for value in obj:
                    found=find_device(value)
                    if found: return found
        device=find_device(qmp('query-pci')); assert device
        bar=next(r['address'] for r in device['regions'] if r['bar']==2)
        assert bar>0
        ts=socket.socket(socket.AF_UNIX); ts.settimeout(15); ts.connect(tmp+'/qtest')
        tf=ts.makefile('rwb',buffering=0)
        def qt(line):
            tf.write((line+'\n').encode()); reply=tf.readline().decode().strip()
            assert reply.startswith('OK'), reply
            return reply[2:].strip()
        def write(addr,data): qt(f'write {addr:#x} {len(data):#x} 0x{data.hex()}')
        def read(addr,length): return bytes.fromhex(qt(f'read {addr:#x} {length:#x}').removeprefix('0x'))
        def payload_bytes(): return qmp('qom-get',{'path':'/machine/peripheral/ct3','property':'store-payload-bytes'})
        def mailbox(ranges, subclass=1, raw=None):
            payload=raw if raw is not None else struct.pack('<BBHI',1,subclass,0,len(ranges))+b''.join(struct.pack('<QQ',*r) for r in ranges)
            write(bar+0x88+0x20,payload)
            qt(f'writeq {bar+0x88+8:#x} {(len(payload)<<16)|0x4402:#x}')
            qt(f'writel {bar+0x88+4:#x} 0x1')
            assert int(qt(f'readl {bar+0x88+4:#x}'),16)&1==0
            return (int(qt(f'readq {bar+0x88+0x10:#x}'),16)>>32)&0xffff
        rng=random.Random(42)
        expected=[bytes([0xab])*4096 if i%2==0 else rng.randbytes(4096) for i in range(128)]
        for i,page in enumerate(expected): write(address+i*4096,page)
        for i,page in enumerate(expected): assert read(address+i*4096,4096)==page
        before=payload_bytes(); assert before>0
        assert mailbox([(10*4096,100*4096),(2**64-4096,8192)])!=0
        assert mailbox([],raw=struct.pack('<BBHI',1,1,0,2)+struct.pack('<QQ',0,4096))!=0
        for i,page in enumerate(expected): assert read(address+i*4096,4096)==page
        results['checks'].append('malformed and overflow batch preserve all data')
        write(address+70*4096,b'changed')
        assert mailbox([(10*4096,100*4096)])==0
        after=payload_bytes(); assert after<before
        expected[10:110]=[bytes(4096)]*100
        for i,page in enumerate(expected): assert read(address+i*4096,4096)==page
        assert mailbox([(10*4096,100*4096)])==0
        results['checks'].append('aligned release drops payload, zeroes reads and preserves neighbors')
        for i in range(10,110):
            expected[i]=rng.randbytes(4096); write(address+i*4096,expected[i])
        for i,page in enumerate(expected): assert read(address+i*4096,4096)==page
        results['checks'].append('reuse and cache eviction preserve every byte')
        assert mailbox([(20*4096+7,33)])==0
        expected[20]=expected[20][:7]+bytes(33)+expected[20][40:]
        assert mailbox([(21*4096+3,19)],subclass=0)==0
        expected[21]=expected[21][:3]+bytes([15])*19+expected[21][22:]
        for i,page in enumerate(expected): assert read(address+i*4096,4096)==page
        results['checks'].append('unaligned zero and sanitize preserve adjacent bytes')
        assert mailbox([(0,128*4096)])==0
        assert payload_bytes()==0
        for i in range(128): assert read(address+i*4096,4096)==bytes(4096)
        results['checks'].append('full release returns stored payload to zero')
        results.update(payload_before_release=before,payload_after_release=after,status='PASS')
        qmp('quit'); proc.wait(timeout=10)
        assert proc.returncode==0
        tf.close();ts.close();qf.close();qs.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        sel.close()
        log=re.sub(rb'\([^\s()]+@[^\s()]+\)',b'(builder@lab)',bytes(transcript))
        (args.output/'guest.txt').write_bytes(log)
(args.output/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
