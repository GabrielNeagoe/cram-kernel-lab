"""Declare Ubuntu's KUnit-visible helper when enabling policy tests."""
from pathlib import Path
p = Path('/srv/cram-lab/linux-apparmor/security/apparmor/include/policy_unpack.h')
s = p.read_text()
anchor = 'bool aa_unpack_u32(struct aa_ext *e, u32 *data, const char *name);'
assert s.count(anchor) == 1
assert 'bool unpack_u16(' not in s
p.write_text(s.replace(anchor, 'bool unpack_u16(struct aa_ext *e, u16 *data, const char *name);\n' + anchor))
