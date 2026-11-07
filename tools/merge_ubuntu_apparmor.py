"""Three-way merge Ubuntu AppArmor changes onto an isolated CRAM checkout.

Preserves newer mainline changes where the textual merge is unambiguous;
conflicts require review before any build or installation.
"""
from pathlib import Path
import subprocess
import shutil
import json

study = Path('/srv/cram-lab/apparmor-study')
base = study / 'mainline-7.0/security/apparmor'
ubuntu = study / 'linux-source-7.0.0/security/apparmor'
target = Path('/srv/cram-lab/linux-apparmor/security/apparmor')
conflicts = []
changed = []
for item in sorted(ubuntu.rglob('*')):
    if not item.is_file():
        continue
    name = item.relative_to(ubuntu)
    old, current = base / name, target / name
    if old.exists() and item.read_bytes() == old.read_bytes():
        continue
    changed.append(str(name))
    if not old.exists():
        if current.exists():
            raise SystemExit(f'Unexpected new-file collision: {name}')
        current.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, current)
    else:
        result = subprocess.run(['git', 'merge-file', '-p', str(current), str(old), str(item)],
                                stdout=subprocess.PIPE)
        if not 0 <= result.returncode <= 127:
            raise SystemExit(f'Merge failed: {name}, {result.returncode}')
        current.write_bytes(result.stdout)
        if result.returncode:
            conflicts.append(str(name))
(study / 'merge-summary.json').write_text(json.dumps({'changed': changed, 'conflicts': conflicts}, indent=2)+'\n')
print(json.dumps({'changed_files': len(changed), 'conflicts': conflicts}, indent=2))
