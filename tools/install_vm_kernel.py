"""Install the validated candidate as an additional boot option; retain stock default.

Run with sudo on the Ubuntu lab VM. Does not reboot or select the candidate.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import time
import sys

def run(*args):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT).stdout

if os.geteuid() != 0:
    raise SystemExit('Run with sudo')
apparmor_candidate = '--apparmor-candidate' in sys.argv
out = Path('/srv/cram-lab/build-linux-vm-aa' if apparmor_candidate
           else '/srv/cram-lab/build-linux-vm')
if '--wait-validation' in sys.argv:
    started = time.time()
    build_started = (out / 'build-started.txt').stat().st_mtime
    print('Waiting for this build and staging checks; no host changes yet.', flush=True)
    while time.time() - started < 7200:
        for name in ('build-exit.txt', 'stage-exit.txt'):
            marker = out / name
            if marker.exists() and marker.stat().st_mtime >= build_started and marker.read_text().strip() != '0':
                raise SystemExit(f'Validation failed: {name}; nothing installed')
        marker = out / 'stage-exit.txt'
        if marker.exists() and marker.stat().st_mtime >= build_started and marker.read_text().strip() == '0':
            break
        time.sleep(5)
    else:
        raise SystemExit('Validation deadline exceeded; nothing installed')
release = (out / 'kernelrelease.txt').read_text().strip()
stock = '7.0.0-38-generic'
expected_release = '7.2.0-rc2-cram-vm-aa1+' if apparmor_candidate else '7.2.0-rc2-cram-vm+'
if release != expected_release:
    raise SystemExit('Unexpected candidate release')
for marker in ('build-exit.txt', 'stage-exit.txt'):
    if (out / marker).read_text().strip() != '0':
        raise SystemExit(f'Validation failed: {marker}')
allowed_running = (stock, '7.2.0-rc2-cram-vm+') if apparmor_candidate else (stock,)
if os.uname().release not in allowed_running:
    raise SystemExit('Unexpected running kernel before installation')
stage = out / 'staging'
destination = Path('/lib/modules') / release
settings = Path('/etc/default/grub.d/99-cram-recovery.cfg')
if destination.exists() or (settings.exists() and not apparmor_candidate) or Path(f'/boot/vmlinuz-{release}').exists():
    raise SystemExit('Candidate installation already exists; inspect instead of overwriting')
if apparmor_candidate:
    import hashlib
    expected_patch = '21f6fa8ad9e90755902c2d8968727c6da1516adba9b17e73b4ce339923c716c6'
    if hashlib.sha256((out / 'source.patch').read_bytes()).hexdigest() != expected_patch:
        raise SystemExit('Unexpected AppArmor source patch')
    for name in ('vmlinuz', 'initrd.img'):
        if not Path(f'/boot/{name}-7.2.0-rc2-cram-vm+').is_file():
            raise SystemExit('First CRAM recovery boot files missing')
for name in ('vmlinuz', 'initrd.img'):
    if not Path(f'/boot/{name}-{stock}').is_file():
        raise SystemExit('Stock recovery boot files missing')
cfg = Path('/boot/grub/grub.cfg').read_text()
submenu = re.search(r"^submenu .*?\$menuentry_id_option '([^']+)'", cfg, re.M)
entry = re.search(r"^\s*menuentry '[^']*with Linux " + re.escape(stock)
                  + r"' .*?\$menuentry_id_option '([^']+)'", cfg, re.M)
if not submenu or not entry:
    raise SystemExit('Cannot identify stock recovery menu entry')
stock_id = submenu[1] + '>' + entry[1]
required = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file() and not p.is_symlink())
if shutil.disk_usage('/').free < required + 2 * 1024**3:
    raise SystemExit('Insufficient installation space plus 2 GiB reserve')
recovery = out / ('recovery-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
recovery.mkdir(mode=0o700)
run('tar', '-C', '/', '-czf', str(recovery / 'boot-and-grub-before.tar.gz'),
    'boot', 'etc/default/grub', 'etc/default/grub.d')
os.chmod(recovery / 'boot-and-grub-before.tar.gz', 0o600)
settings.write_text('# Experimental CRAM boot option; keep stock kernel as default.\n'
                    + "GRUB_DEFAULT='" + stock_id + "'\n"
                    + 'GRUB_TIMEOUT_STYLE=menu\nGRUB_TIMEOUT=10\n')
shutil.copytree(stage / 'lib/modules' / release, destination, symlinks=True)
for name in ('vmlinuz', 'System.map', 'config'):
    target = Path('/boot') / f'{name}-{release}'
    shutil.copy2(stage / 'boot' / target.name, target)
    target.chmod(0o600 if name == 'System.map' else 0o644)
print(run('depmod', release), flush=True)
print(run('update-initramfs', '-c', '-k', release), flush=True)
initrd = Path(f'/boot/initrd.img-{release}')
listing = run('lsinitramfs', str(initrd))
for needed in ('e1000.ko', 'vmw_vmci.ko'):
    # Network/VMCI need not be in initramfs, but must exist in installed modules.
    if not any(p.name.startswith(needed) for p in destination.rglob('*')):
        raise SystemExit(f'Missing installed module {needed}')
if f'lib/modules/{release}/' not in listing:
    raise SystemExit('Candidate initramfs lacks its module tree')
print(run('update-grub'), flush=True)
generated = Path('/boot/grub/grub.cfg').read_text()
if f'with Linux {release}' not in generated or f'with Linux {stock}' not in generated:
    raise SystemExit('Generated menu is missing a required kernel')
if stock_id not in generated:
    raise SystemExit('Stock default not present in generated GRUB configuration')
print(run('grub-script-check', '/boot/grub/grub.cfg'), flush=True)
manifest = {'candidate': release, 'stock_default': stock, 'recovery_backup': str(recovery),
            'rebooted': False, 'initramfs_bytes': initrd.stat().st_size}
import json
(out / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest), flush=True)
