"""Build the bounded driver with an explicit hold gate for host-request tests."""
from pathlib import Path
import difflib
import runpy

root = Path(__file__).resolve().parents[1]
runpy.run_path(str(root/'tools/build_targeted_balloon.py'))
old = (root/'private/targeted-build/vmw_balloon.c').read_text()
text = old
def replace(a, b):
    global text
    assert text.count(a) == 1, a
    text = text.replace(a, b)

replace('static DEFINE_MUTEX(cram_pool_lock);',
        'static bool cram_hold = true;\nmodule_param(cram_hold, bool, 0600);\nstatic DEFINE_MUTEX(cram_pool_lock);')
replace('if (!list_empty(&cram_pool)) {',
        'if (!READ_ONCE(cram_hold) && !list_empty(&cram_pool)) {')
replace('if (atomic64_read(&b->size) || b->target || READ_ONCE(cram_offer_until)) {',
        'if (atomic64_read(&b->size) || !READ_ONCE(cram_hold) || READ_ONCE(cram_offer_until)) {')
out = root/'private/startup-build'
out.mkdir(exist_ok=True)
(out/'vmw_balloon.c').write_text(text, encoding='utf-8')
(out/'Makefile').write_text('obj-m += vmw_balloon.o\n', encoding='utf-8')
(root/'patches/vmw-balloon-startup-hold.patch').write_text(''.join(difflib.unified_diff(
    old.splitlines(True), text.splitlines(True),
    fromfile='a/targeted/vmw_balloon.c', tofile='b/targeted/vmw_balloon.c')), encoding='utf-8')
