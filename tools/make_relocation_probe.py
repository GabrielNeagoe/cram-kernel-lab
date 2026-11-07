"""Derive a forced-relocation variant without modifying the kernel checkout."""
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
old = '\tq = mremap(p, len, len, MREMAP_MAYMOVE);'
new = '''	/* Reserve a distinct destination that belongs to this test only. */
	void *destination = mmap(NULL, len, PROT_NONE,
				 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (destination == MAP_FAILED) {
		munmap(p, len);
		return 2;
	}
	q = mremap(p, len, len, MREMAP_MAYMOVE | MREMAP_FIXED, destination);
	if (q == MAP_FAILED) {
		munmap(destination, len);
		munmap(p, len);
		return 2;
	}
	if (q == p || q != destination)
		rc = 1;'''
if source.count(old) != 1:
    raise SystemExit('Unexpected upstream mremap source; refusing replacement')
Path(sys.argv[2]).write_text(source.replace(old, new))
