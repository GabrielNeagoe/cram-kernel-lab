"""Extend the startup-guarded module for a separate capped full-request trial."""
from pathlib import Path
import difflib

root = Path(__file__).resolve().parents[1]
old = (root/'private/startup-build/vmw_balloon.c').read_text()
s = old
def change(a,b):
    global s
    assert s.count(a)==1, a
    s=s.replace(a,b)

# Always expose the unmodified host target in debugfs. Bound only the local goal.
a=s.index('\ttarget = READ_ONCE(b->target);'); z=s.index('\n\t/*',a)
assert 'cram_offer_until' in s[a:z]
s=s[:a]+'''\ttarget = READ_ONCE(cram_hold) ? 0 : min_t(unsigned long,
\t\tREAD_ONCE(b->target), (4UL << 30) >> PAGE_SHIFT);
'''+s[z:]
change('page = cram_pool_take();','''{
\t\t\t\tpage = cram_pool_take();
\t\t\t\tif (!page && !READ_ONCE(cram_hold) &&
\t\t\t\t    atomic64_read(&b->size) + ctl->n_refused_pages + i +
\t\t\t\t    READ_ONCE(cram_pending) < ((4UL << 30) >> PAGE_SHIFT))
\t\t\t\t\tpage = balloon_page_alloc();
\t\t\t}''')
# Do not round down small deflation requests: we want to measure exact matches.
a=s.index('\t/* consider a 2MB slack');z=s.index('\t/* If an out-of-memory',a);s=s[:a]+s[z:]
assert '.page_size = VMW_BALLOON_4K_PAGE,' in s and '.page_size = b->max_page_size,' not in s
out=root/'private/full-request-build';out.mkdir(exist_ok=True)
(out/'vmw_balloon.c').write_text(s,encoding='utf-8')
(out/'Makefile').write_text('obj-m += vmw_balloon.o\n',encoding='utf-8')
(root/'patches/vmw-balloon-full-request.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='a/guarded/vmw_balloon.c',tofile='b/guarded/vmw_balloon.c')),encoding='utf-8')

# A second variant removes ALL demand allocation from inflation. Prepare and
# touch a finite reservoir before the baseline, with original CRAM frames first.
a=s.index('\t\t\telse\n\t\t\t\t{\n\t\t\t\tpage = cram_pool_take();');z=s.index('\n\n',a)
s=s[:a]+'\t\t\telse\n\t\t\t\tpage = cram_pool_take();'+s[z:]
insert='''static int cram_preback_set(const char *val, const struct kernel_param *kp)
{
    unsigned long i;
    int err = 0;
    if (!sysfs_streq(val, "1"))
        return -EINVAL;
    down_write(&balloon.conf_sem);
    mutex_lock(&cram_pool_lock);
    if (!READ_ONCE(cram_hold) || atomic64_read(&balloon.size) ||
        !cram_pending || cram_pending != cram_acquired || cram_pending > 8192) {
        err = -EBUSY;
        goto out;
    }
    for (i = cram_pending; i < ((4UL << 30) >> PAGE_SHIFT); i++) {
        struct page *page = alloc_page(GFP_KERNEL | __GFP_NORETRY | __GFP_NOWARN);
        if (!page) {
            err = -ENOMEM;
            break;
        }
        memset64(page_address(page), 0x15a07abc00000000ULL + i, PAGE_SIZE / 8);
        list_add_tail(&page->lru, &cram_pool);
        cram_pending++;
        cond_resched();
    }
out:
    mutex_unlock(&cram_pool_lock);
    up_write(&balloon.conf_sem);
    return err;
}
static const struct kernel_param_ops cram_preback_ops = { .set = cram_preback_set };
module_param_cb(cram_preback, &cram_preback_ops, NULL, 0200);

'''
change('static ssize_t cram_frames_write(',insert+'static ssize_t cram_frames_write(')
assert 'page = balloon_page_alloc();' not in s
dest=root/'private/preback-request-build';dest.mkdir(exist_ok=True)
(dest/'vmw_balloon.c').write_text(s,encoding='utf-8');(dest/'Makefile').write_text('obj-m += vmw_balloon.o\n',encoding='utf-8')
(root/'patches/vmw-balloon-preback-request.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='a/guarded/vmw_balloon.c',tofile='b/guarded/vmw_balloon.c')),encoding='utf-8')
