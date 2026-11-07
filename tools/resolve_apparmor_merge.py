"""Apply reviewed resolutions to the one-time Ubuntu 7.0 / CRAM 7.2 merge."""
from pathlib import Path
import re
import shutil

root = Path('/srv/cram-lab/linux-apparmor')
choices = {
    'apparmorfs.c': [1, 2],  # Keep newer raw-data ref helper; Ubuntu feature spelling.
    'domain.c': [2, 1, 1, 1],  # New audit argument; retain newer error-pointer contracts.
    'include/lib.h': [1],  # Equivalent NULL initializer; retain mainline formatting.
    'lsm.c': [2],  # Ubuntu supplies replacement receive hook later in this file.
    'net.c': [2, 2],  # Actual Ubuntu network implementation and compatibility features.
}
pattern = re.compile(r'^<<<<<<<[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n', re.M | re.S)
for name, decisions in choices.items():
    path = root / 'security/apparmor' / name
    source = path.read_text()
    matches = list(pattern.finditer(source))
    if len(matches) != len(decisions):
        raise SystemExit(f'Unexpected conflicts: {name}')
    for match, choice in reversed(list(zip(matches, decisions))):
        source = source[:match.start()] + match[choice] + source[match.end():]
    path.write_text(source)
uapi = root / 'include/uapi/linux/apparmor.h'
if uapi.exists():
    raise SystemExit('Unexpected pre-existing notification UAPI')
shutil.copyfile('/srv/cram-lab/apparmor-study/linux-source-7.0.0/include/uapi/linux/apparmor.h', uapi)
path = root / 'security/apparmor/Kconfig'
source = path.read_text()
source = source.replace('bool "Enable AppArmor packet mediation by defauit\n',
                        'bool "Enable AppArmor packet mediation by default"\n')
path.write_text(source)
