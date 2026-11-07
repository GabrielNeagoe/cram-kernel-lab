"""Install release mailbox code in the isolated QEMU source, preserving baseline."""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1])
source = Path(sys.argv[2])
ct3 = root / 'hw/cxl/compressed_type3/ct3.c'
text = ct3.read_text()
assert '#include "ct3-media-release.inc"' not in text
assert '#include "ct3-store.inc"' in text
backup = ct3.with_suffix('.c.before-release')
assert not backup.exists()
shutil.copyfile(ct3, backup)
start = text.index('static CXLRetCode cmd_ct3_media_operations(')
end = text.index('static const struct cxl_cmd cxl_cmd_set_ct3', start)
text = text[:start] + '#include "ct3-media-release.inc"\n\n' + text[end:]
text = text.replace('#include "ct3-store.inc"',
                    '#include "ct3-store.inc"\n#include "ct3-store-release.inc"')
for name in ('ct3-store-release.inc', 'ct3-media-release.inc'):
    shutil.copyfile(source / name, ct3.parent / name)
ct3.write_text(text)
print('Release mailbox source installed in isolated checkout')
