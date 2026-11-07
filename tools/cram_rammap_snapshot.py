"""Reduce a RAMMap 1.63 export to physical-page identities for the two trial files only."""
import binascii
import re
from pathlib import Path
import numpy as np

def read_snapshot(path, targets, *, include_keys=False):
    # Validate the export layout before interpreting x64 records. File offsets and
    # frame IDs are independently checked with a small locked calibration mapping.
    keys = {name:set() for name in targets}
    with Path(path).open('rb') as f:
        assert f.readline().strip()==b'<?xml version="1.0"?>' and f.readline().strip()==b'<root Application="RamMap" Version="1.0" Architecture="amd64">', 'unrecognized RAMMap header'
        tail=b''
        while True:
            block=f.read(2**20)
            if not block: raise ValueError('missing physical-page table')
            chunk=tail+block
            marker=chunk.find(b'<PfnDatabase>')
            prefix=chunk if marker<0 else chunk[:marker]
            for name in targets:
                needle=name.encode()+b'"'
                for hit in re.finditer(re.escape(needle),prefix,re.I):
                    tag=prefix[max(0,hit.start()-1024):hit.start()]
                    found=re.search(rb'<File Key="(-?\d+)" Path="[^"<>]*$',tag)
                    if found: keys[name].add(int(found[1]) % 2**64)
            if marker>=0:
                f.seek(marker+13-len(chunk),1)
                break
            tail=chunk[-2048:]
        assert all(len(v)==1 for v in keys.values()),'trial file key absent or ambiguous'
        final=b'</PfnDatabase>'
        result={name:{} for name in targets}
        while True:
            chunk=f.read(48*65536)
            end=chunk.find(b'<')
            if end>=0:
                assert (chunk[end:]+f.read(64)).startswith(final), 'incomplete snapshot'
                chunk=chunk[:end]
            assert chunk and len(chunk)%48==0 or end==0
            rows=np.frombuffer(binascii.unhexlify(chunk),dtype='<u8').reshape(-1,3)
            for name, wanted in targets.items():
                key=next(iter(keys[name]))
                selected=rows[(rows[:,2]==key)&((rows[:,0]&15)==1)]
                offsets=((selected[:,0]>>12)&((1<<45)-1))*4096
                hits=np.isin(offsets,wanted)
                for offset,frame in zip(offsets[hits],selected[hits,1]):
                    result[name].setdefault(int(offset),set()).add(int(frame))
            if end>=0:
                if include_keys:
                    return result, {name:next(iter(values)) for name,values in keys.items()}
                return result

