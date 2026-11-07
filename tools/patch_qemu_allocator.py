"""Add glibc allocator diagnostics to the isolated experimental QEMU checkout."""
from pathlib import Path
import shutil

root = Path('/srv/cram-lab/qemu-compressed/hw/cxl/compressed_type3')
p = root/'ct3.c'
s = p.read_text()
assert 'ct3_allocator_diagnostics_init' not in s
s = s.replace('#include "ct3-store.inc"', '#include "ct3-store.inc"\n#include "ct3-allocator-diagnostics.inc"')
needle = '        address_space_destroy(&s->ct3d.hostvmem_as);'
assert s.count(needle) == 1
s = s.replace(needle, '        ct3_allocator_diagnostics_init(OBJECT(s));\n'+needle)
shutil.copyfile(Path(__file__).with_name('ct3-allocator-diagnostics.inc'), root/'ct3-allocator-diagnostics.inc')
p.write_text(s)
