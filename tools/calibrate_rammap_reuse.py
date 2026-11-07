"""Compare RAMMap metadata against two cooperative owners of reallocated AWE frames."""
import argparse
import binascii
import ctypes
import json
import multiprocessing as mp
import os
import re
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psutil

from calibrate_windows_frame_reuse import owned_pages
from test_cram_backing_changes import take_snapshot
from cram_owner_sampler import _identity_batch, profiling_privilege


@contextmanager
def mapped_pages(count):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_uint]
    kernel.VirtualAlloc.restype = ctypes.c_void_p
    kernel.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
    kernel.MapUserPhysicalPages.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    address = kernel.VirtualAlloc(None, count * 4096, 0x00402000, 4)  # MEM_PHYSICAL | MEM_RESERVE
    if not address:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with owned_pages(count) as pages:
            if not kernel.MapUserPhysicalPages(address, count, pages):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield address, list(pages)
            finally:
                if not kernel.MapUserPhysicalPages(address, count, None):
                    raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel.VirtualFree(address, 0, 0x8000):
            raise ctypes.WinError(ctypes.get_last_error())


def receiver(pipe):
    with pipe:
        pipe.send(os.getpid())
        originals = pipe.recv()
        pipe.send("ready")
        pipe.recv()
        for attempt in range(1, 65):
            with mapped_pages(65536) as (address, pages):
                if originals.intersection(pages):
                    pipe.send((attempt, address, pages))
                    if not pipe.poll(300):
                        raise TimeoutError("Observer did not finish its second snapshot")
                    pipe.recv()
                    return
            time.sleep(0.1)
        pipe.send(None)


def inspect(path, wanted, pids):
    """Keep only test-frame rows and the two test-process XML tags for local analysis."""
    with path.open("rb") as f:
        if b'Application="RamMap"' not in f.readline() + f.readline():
            raise ValueError("Unrecognized snapshot")
        header = bytearray()
        marker = b"<PfnDatabase>"
        while marker not in header:
            block = f.read(1024 * 1024)
            if not block or len(header) > 128 * 2**20:
                raise ValueError("Missing physical page table")
            header.extend(block)
        pos = header.index(marker)
        f.seek(pos + len(marker) - len(header), 1)
        tags = [m.decode(errors="replace") for m in
                re.findall(rb"<Process\s[^<>]*>", header[:pos])
                if any(re.search(rb'(?i)\b(?:ProcessId|Pid|Id)="%d"' % pid, m) for pid in pids)]
        hits = {}
        while True:
            chunk = f.read(48 * 65536)
            end = chunk.find(b"<")
            if end >= 0:
                if not (chunk[end:] + f.read(32)).startswith(b"</PfnDatabase>"):
                    raise ValueError("Invalid physical page table terminator")
                chunk = chunk[:end]
            if len(chunk) % 48 or (not chunk and end < 0):
                raise ValueError("Incomplete physical page table")
            rows = np.frombuffer(binascii.unhexlify(chunk), dtype="<u8").reshape(-1, 3)
            for flags, frame, info in rows[np.isin(rows[:, 1], wanted)]:
                frame = int(frame)
                if frame in hits:
                    raise ValueError("Ambiguous physical identity")
                hits[frame] = [int(flags), int(info)]
            if end >= 0:
                return hits, tags


def run(exe, output, fast=False):
    # The child inherits this affinity. RAMMap is restored to the original CPU set below.
    process = psutil.Process()
    affinity = process.cpu_affinity()
    process.cpu_affinity(affinity[:1])
    parent, child = mp.Pipe()
    worker = mp.Process(target=receiver, args=(child,))
    worker.start()
    child.close()
    try:
        pid_b = parent.recv()
        with tempfile.TemporaryDirectory(dir=output) as tmp:
            a, b = [Path(tmp) / f"{name}.rmp" for name in ("a", "b")]
            stop = output / "stop"
            with mapped_pages(32768) as (va_a, pages_a):
                parent.send(set(pages_a))
                if parent.recv() != "ready":
                    raise RuntimeError("Second owner did not acknowledge the frame list")
                process.cpu_affinity(affinity)
                if fast:
                    with profiling_privilege():
                        first_seen = {}
                        for start in range(0, len(pages_a), 1024):
                            batch = _identity_batch(pages_a[start:start+1024])()
                            first_seen.update({int(frame): int(flags) for flags, frame, _ in batch})
                else:
                    take_snapshot(exe, a, stop)
                process.cpu_affinity(affinity[:1])
            parent.send(True)
            process.cpu_affinity(affinity)
            if not parent.poll(30):
                raise TimeoutError("Second allocation timed out")
            reply = parent.recv()
            if reply is None:
                return {"known_reuses": 0, "attempts": 64}
            attempts, va_b, pages_b = reply
            common = sorted(set(pages_a).intersection(pages_b))
            if fast:
                with profiling_privilege():
                    eligible = common[:1024]
                    batch = _identity_batch(eligible)()
                    confirmed = sum((first_seen[int(frame)] & 15) == (int(flags) & 15) == 9
                                    and first_seen[int(frame)] >> 9 == os.getpid()
                                    and int(flags) >> 9 == pid_b for flags, frame, _ in batch)
            else:
                take_snapshot(exe, b, stop)
            parent.send(True)
            worker.join(10)
            if worker.exitcode != 0:
                raise RuntimeError("Second owner cleanup failed")
            if fast:
                return dict(known_reuses=len(common), checked=len(eligible), confirmed=confirmed)
            wanted = np.array(common, dtype=np.uint64)
            pids = os.getpid(), pid_b
            first, tags_a = inspect(a, wanted, pids)
            second, tags_b = inspect(b, wanted, pids)
            # Raw identifiers stay private for decoding. Never include raw snapshots in Git.
            (output / "selected.json").write_text(json.dumps(dict(
                pids=pids, first=first, second=second, first_tags=tags_a, second_tags=tags_b,
                va_a=va_a, va_b=va_b, pages_a=pages_a, pages_b=pages_b)))
            return dict(known_reuses=len(common), attempts=attempts, first_rows=len(first),
                        second_rows=len(second), comparable=len(first.keys() & second.keys()))
    finally:
        process.cpu_affinity(affinity)
        parent.close()
        if worker.is_alive():
            worker.terminate()
            worker.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("rammap", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.rammap.resolve(), args.output.resolve(), args.fast)
        (args.output / "result.json").write_text(json.dumps(result))
    except Exception:
        (args.output / "error.txt").write_text(traceback.format_exc())
        raise
