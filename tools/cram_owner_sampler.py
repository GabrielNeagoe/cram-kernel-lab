"""Sample ownership metadata for the marker-verified, unlocked CRAM cohort."""
import contextlib
import ctypes as C
import json
import time

import numpy as np
import win32api
import win32con
import win32security


@contextlib.contextmanager
def profiling_privilege():
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
    privilege = win32security.LookupPrivilegeValue(None, "SeProfileSingleProcessPrivilege")
    previous = win32security.AdjustTokenPrivileges(token, False, [(privilege, 2)])
    try:
        yield
    finally:
        win32security.AdjustTokenPrivileges(token, False, previous)
        token.Close()


def _identity_batch(frames):
    class Envelope(C.Structure):
        _fields_ = [("version", C.c_uint32), ("magic", C.c_uint32), ("operation", C.c_uint32),
                    ("payload", C.c_void_p), ("length", C.c_uint32)]

    if not 1 <= len(frames) <= 1024 or len(set(frames)) != len(frames):
        raise ValueError("Expected a unique, bounded test cohort")
    # x64 PF_PFN_PRIO_REQUEST v1, followed by its variable-length page identities.
    header = 16 + 22 * 8
    payload = C.create_string_buffer(header + 24 * len(frames))
    C.c_uint32.from_buffer(payload).value = 1
    C.c_size_t.from_buffer(payload, 8).value = len(frames)
    rows = np.ndarray((len(frames), 3), dtype="<u8", buffer=payload, offset=header)
    ids = np.array(frames, dtype=np.uint64)
    env = Envelope(45, 0x6b756843, 6, C.addressof(payload), len(payload))
    query = C.WinDLL("ntdll").NtQuerySystemInformation
    query.argtypes = [C.c_uint32, C.c_void_p, C.c_uint32, C.c_void_p]
    query.restype = C.c_int32

    def read():
        rows.fill(0)
        rows[:, 1] = ids
        status = query(79, C.byref(env), C.sizeof(env), None)
        if status or not np.array_equal(rows[:, 1], ids):
            raise RuntimeError(f"Physical metadata query failed, status 0x{status & 0xffffffff:08x}")
        return rows
    return read


def backing_state(flags, info, file_key, expected):
    kind = flags & 15
    page_list = (flags >> 4) & 7
    if page_list in (0, 1):
        return "zeroed" if page_list == 0 else "free"
    if kind == 1 and info:
        if info != file_key:
            return "different-file"
        return "original" if ((flags >> 12) & ((1 << 45) - 1)) == expected else "different-offset"
    if kind in (0, 9) and page_list == 6 and ((flags >> 9) & ((1 << 48) - 1)):
        return "process-backed"
    return "other-or-unresolved"


def record(folder, frames, offsets, file_key, stop, deadline):
    count = len(frames)
    if count not in (1024, 8192) or len(set(offsets)) != count or len(set(frames)) != count:
        raise ValueError("Invalid verified cohort")
    expected = np.asarray(offsets, dtype=np.uint64) // 4096
    with profiling_privilege(), (folder / "ownership.jsonl").open("x") as stream:
        reads = [_identity_batch(frames[start:start+1024]) for start in range(0,count,1024)]
        first = True
        while not stop.exists() and not (folder / "stop").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Physical ownership sampling timed out")
            start = time.time()
            rows = np.concatenate([read() for read in reads])
            end = time.time()
            flags = rows[:, 0]
            kind = flags & 15
            page_list = (flags >> 4) & 7
            original = ((kind == 1) & (rows[:, 2] == file_key)
                        & (((flags >> 12) & ((1 << 45) - 1)) == expected))
            minimum_baseline = count if count == 1024 else count * .99
            if first and int(original.sum()) < minimum_baseline:
                raise RuntimeError("Live baseline disagrees with RAMMap file ownership")
            first = False
            # No addresses, host frame identifiers, process IDs or file keys leave this observer.
            other = []
            for i in np.flatnonzero(~original):
                entry = [int(i), int(kind[i]), int(page_list[i])]
                if count == 8192:
                    entry.append(backing_state(int(flags[i]), int(rows[i,2]), file_key, int(expected[i])))
                other.append(entry)
            available = np.flatnonzero((page_list == 0) | (page_list == 1)).tolist()
            print(json.dumps([start, end, int(original.sum()), other, available],
                             separators=(",", ":")), file=stream)
            if not (folder / "ownership-ready").exists():
                stream.flush()
                (folder / "ownership-ready").touch()
            time.sleep(.02)
