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


def record(folder, frames, offsets, file_key, stop, deadline):
    if len(frames) != 1024 or len(set(offsets)) != 1024:
        raise ValueError("Invalid unlocked cohort")
    expected = np.asarray(offsets, dtype=np.uint64) // 4096
    with profiling_privilege(), (folder / "ownership.jsonl").open("x") as stream:
        read = _identity_batch(frames)
        first = True
        while not stop.exists() and not (folder / "stop").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Physical ownership sampling timed out")
            start = time.time()
            rows = read()
            end = time.time()
            flags = rows[:, 0]
            kind = flags & 15
            page_list = (flags >> 4) & 7
            original = ((kind == 1) & (rows[:, 2] == file_key)
                        & (((flags >> 12) & ((1 << 45) - 1)) == expected))
            if first and not original.all():
                raise RuntimeError("Live baseline disagrees with RAMMap file ownership")
            first = False
            # No addresses, host frame identifiers, process IDs or file keys leave this observer.
            other = [(int(i), int(kind[i]), int(page_list[i])) for i in np.flatnonzero(~original)]
            available = np.flatnonzero((page_list == 0) | (page_list == 1)).tolist()
            print(json.dumps([start, end, int(original.sum()), other, available],
                             separators=(",", ":")), file=stream)
            if not (folder / "ownership-ready").exists():
                stream.flush()
                (folder / "ownership-ready").touch()
            time.sleep(.02)
