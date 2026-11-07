"""Disable transparent huge pages only for this process, then exec the command."""
import ctypes
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(41, 1, 0, 0, 0) != 0 or libc.prctl(42, 0, 0, 0, 0) != 1:
    raise OSError(ctypes.get_errno(), 'Cannot disable transparent huge pages')
os.execv(sys.argv[1], sys.argv[1:])
