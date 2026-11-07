"""Produce independent ground truth for a frame observer using only test-owned pages."""
import ctypes
import json
import multiprocessing as mp
import os
import time
from contextlib import contextmanager

import win32api
import win32con
import win32security


@contextmanager
def owned_pages(count):
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY
    )
    privilege = win32security.LookupPrivilegeValue(None, "SeLockMemoryPrivilege")
    previous = win32security.AdjustTokenPrivileges(token, False, [(privilege, 2)])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    word = ctypes.c_size_t
    args = [ctypes.c_void_p, ctypes.POINTER(word), ctypes.POINTER(word)]
    allocate = kernel.AllocateUserPhysicalPages
    release = kernel.FreeUserPhysicalPages
    allocate.argtypes = release.argtypes = args
    frames = (word * count)()
    size = word(count)
    try:
        if not allocate(-1, ctypes.byref(size), frames):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if size.value != count:
                raise RuntimeError("Partial calibration allocation")
            yield frames
        finally:
            if not release(-1, ctypes.byref(size), frames):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        win32security.AdjustTokenPrivileges(token, False, previous)
        token.Close()


def second_owner(pipe):
    with pipe:
        pipe.send(os.getpid())
        original = pipe.recv()
        for attempt in range(1, 33):
            with owned_pages(16384) as frames:
                common = original.intersection(frames)
                acquired_ns = time.time_ns()
            if common:
                break
        pipe.send(dict(attempts=attempt, shared_frame_numbers=len(common),
                       second_acquisition_observed_ns=acquired_ns))


def main():
    parent, child = mp.Pipe()
    worker = mp.Process(target=second_owner, args=(child,))
    worker.start()
    child.close()
    try:
        child_pid = parent.recv()
        with owned_pages(4096) as frames:
            original = set(frames)
        released_ns = time.time_ns()
        parent.send(original)
        if not parent.poll(30):
            raise TimeoutError("Second owner did not report")
        result = parent.recv()
        worker.join(5)
        if worker.exitcode != 0:
            raise RuntimeError("Second owner failed to exit cleanly")
        print(json.dumps(dict(first_pid=os.getpid(), second_pid=child_pid,
                              first_pages=len(original), second_pages_per_attempt=16384,
                              first_release_completed_ns=released_ns, **result)))
    finally:
        parent.close()
        if worker.is_alive():
            worker.terminate()
            worker.join()


if __name__ == "__main__":
    main()
