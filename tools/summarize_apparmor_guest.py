"""Validate the focused AppArmor guest transcript and redact build identity."""
from pathlib import Path
import json
import re

raw = Path('private/apparmor-prompt-final.log').read_text()
raw = re.sub(r'\([^\s()]+@[^\s()]+\)', '(builder@lab)', raw)
raw = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', raw)
required = ['APPARMOR_POLICY_ALLOW_DENY=PASS', 'NOTIFY_FILTER_V3=PASS',
            'NOTIFY_FILTER_V5=PASS', 'NOTIFY_INVALID_VERSION=PASS',
            'NOTIFY_REGISTER=PASS', 'NOTIFY_EMPTY_RESEND=PASS',
            'NOTIFY_PROMPT_ALLOW=PASS', 'NOTIFY_PROMPT_DENY=PASS',
            'APPARMOR_NETWORK_ALLOW=PASS', 'APPARMOR_NETWORK_DENY=PASS',
            'SNAP_PREFERRED_KERNEL_FEATURES=PASS',
            'NETWORK_ABI_3.0=PASS', 'NETWORK_ABI_4.0=PASS', 'NETWORK_ABI_5.0=PASS',
            'APPARMOR_EXIT=0', 'STUDY_DONE', 'QEMU_EXIT=0']
for marker in required:
    assert re.search('^' + re.escape(marker) + '$', raw, re.M), marker
assert 'apparmor_policy_unpack: pass:30 fail:0 skip:0 total:30' in raw
assert not re.search(r'Kernel panic|BUG:|Oops:', raw)
Path('evidence/apparmor-functional.txt').write_text(raw)
Path('evidence/apparmor-functional.json').write_text(json.dumps({
    'kernel': '7.2.0-rc2-cram-aa-test+',
    'kunit': {'pass': 30, 'fail': 0, 'skip': 0},
    'checks': required[:-3],
    'qemu_exit': 0,
    'scope': 'Disposable BusyBox guest with Ubuntu AppArmor parser; not full Snap userspace',
}, indent=2) + '\n')
for name in ('apparmor-stock-guest', 'apparmor-stock-unversioned', 'apparmor-port-unversioned'):
    baseline = Path('private', name + '.log').read_text()
    baseline = re.sub(r'\([^\s()]+@[^\s()]+\)', '(builder@lab)', baseline)
    baseline = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', baseline)
    assert re.search(r'^QEMU_EXIT=0$', baseline, re.M)
    expected = 1 if 'unversioned' in name else 0
    assert re.search(f'^APPARMOR_EXIT={expected}$', baseline, re.M)
    Path('evidence', name + '.txt').write_text(baseline)
