"""Build a disposable guest overlay using the VM's AppArmor userspace."""
from pathlib import Path
import re
import shutil
import subprocess
import gzip
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--unversioned', action='store_true')
args = parser.parse_args()

base = Path('/srv/cram-lab/apparmor-study')
overlay = base / 'overlay'
overlay.mkdir(exist_ok=True)
def copy(path):
    dst = overlay / path.lstrip('/')
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
for binary in ['/usr/sbin/apparmor_parser', '/usr/bin/aa-exec']:
    copy(binary)
    output = subprocess.check_output(['ldd', binary], text=True)
    for library in re.findall(r'(/[\w/+.\-]+)', output):
        copy(library)
for abi in ('3.0', '4.0', '5.0'):
    copy('/etc/apparmor.d/abi/' + abi)
(overlay / 'bin').mkdir(exist_ok=True)
headers = base / 'probe-include/linux'
headers.mkdir(parents=True, exist_ok=True)
shutil.copy2('/srv/cram-lab/linux-apparmor/include/uapi/linux/apparmor.h', headers)
subprocess.run(['gcc', '-static', '-Wall', '-Wextra', '-Werror',
                '-I' + str(headers.parent),
                str(base / 'apparmor_notify_probe.c'), '-o',
                str(overlay / 'bin/apparmor-notify-probe')], check=True)
subprocess.run(['gcc', '-static', '-Wall', '-Wextra', '-Werror',
                str(base / 'apparmor_network_probe.c'), '-o',
                str(overlay / 'bin/apparmor-network-probe')], check=True)
(overlay / 'aa-policy').write_text('''abi <abi/5.0>,
profile cram-aa-check {
  /bin/busybox rix,
  /allowed r,
  deny /denied r,
}
profile cram-aa-prompt flags=(prompt) {
  /bin/busybox rix,
  /dev/** rw,
  signal (receive) peer=unconfined,
}
profile cram-aa-network-allow {
  /bin/apparmor-network-probe rix,
  network inet stream,
}
profile cram-aa-network-deny {
  /bin/apparmor-network-probe rix,
  deny network inet stream,
}
''')
(overlay / 'aa-check.sh').write_text('''#!/bin/sh
set -eu
mount -t securityfs securityfs /sys/kernel/security || true
test -e /sys/kernel/security/apparmor/features/network/af_mask
for feature in caps dbus domain file mount namespaces network ptrace signal; do
  test -d /sys/kernel/security/apparmor/features/$feature
done
echo SNAP_PREFERRED_KERNEL_FEATURES=PASS
echo allowed > /allowed
echo denied > /denied
echo prompted > /prompt-allow
echo denied > /prompt-deny
/usr/sbin/apparmor_parser -Q -T /aa-policy
/usr/sbin/apparmor_parser -a -T /aa-policy
/usr/bin/aa-exec -p cram-aa-check -- /bin/busybox cat /allowed
if /usr/bin/aa-exec -p cram-aa-check -- /bin/busybox cat /denied; then
  echo POLICY_DENIAL_FAILED
  exit 1
fi
echo APPARMOR_POLICY_ALLOW_DENY=PASS
/bin/apparmor-notify-probe prompt
/usr/bin/aa-exec -p cram-aa-network-allow -- /bin/apparmor-network-probe
/usr/bin/aa-exec -p cram-aa-network-deny -- /bin/apparmor-network-probe deny
for abi in 3.0 4.0 5.0; do
  /bin/busybox sed "1cabi <abi/$abi>," /aa-policy > /aa-policy-versioned
  /usr/sbin/apparmor_parser -r -T /aa-policy-versioned
  /usr/bin/aa-exec -p cram-aa-network-allow -- /bin/apparmor-network-probe
  /usr/bin/aa-exec -p cram-aa-network-deny -- /bin/apparmor-network-probe deny
  echo NETWORK_ABI_$abi=PASS
done
''')
(overlay / 'aa-check.sh').chmod(0o755)
if args.unversioned:
    policy = overlay / 'aa-policy'
    policy.write_text(policy.read_text().removeprefix('abi <abi/5.0>,\n'))
# Concatenated compressed cpio archives are supported by the kernel unpacker.
paths = subprocess.check_output(['find', '.', '-print0'], cwd=overlay)
archive = subprocess.run(['cpio', '--null', '-o', '-H', 'newc'],
                         input=paths, stdout=subprocess.PIPE, cwd=overlay, check=True).stdout
original = Path('/srv/cram-lab/deploy-selftests-20261024/placement.cpio.gz')
name = 'apparmor-unversioned.cpio.gz' if args.unversioned else 'apparmor.cpio.gz'
(base / name).write_bytes(original.read_bytes() + gzip.compress(archive))
print('AppArmor guest overlay ready')
