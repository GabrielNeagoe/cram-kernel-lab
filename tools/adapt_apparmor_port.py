"""Reviewed build adaptations after the initial AppArmor merge."""
from pathlib import Path
root = Path('/srv/cram-lab/linux-apparmor')
p = root / 'security/apparmor/apparmorfs.c'
s = p.read_text()
start = s.index('#ifdef CONFIG_SECURITY_APPARMOR_EXPORT_BINARY\nstatic struct aa_loaddata *get_loaddata_common_ref')
end = s.index('#endif\n', start) + len('#endif\n')
assert s.count('static struct aa_loaddata *get_loaddata_common_ref') == 2
s = s[:start] + s[end:]
p.write_text('\n'.join(line.rstrip() for line in s.splitlines())+'\n')
p = root / 'include/linux/lsm_hooks.h'
s = p.read_text()
needle = '\tunsigned int lbs_bpf_token;\n'
assert s.count(needle) == 1
p.write_text(s.replace(needle, needle+'\tbool lbs_secmark; /* expressed desire for secmark use */\n'))
