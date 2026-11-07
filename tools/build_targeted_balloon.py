"""Construct a separate lab module; never modify the installed native driver."""
from pathlib import Path
import difflib

root = Path(__file__).resolve().parents[1]
source = (root/'private/vmw_balloon-native.c').read_text()
text = source
def replace(old, new):
    global text
    assert text.count(old) == 1, old
    text = text.replace(old, new)

replace('#include <linux/slab.h>', '#include <linux/slab.h>\n#include <linux/uaccess.h>\n#include <linux/gfp.h>\n#include <linux/pageblock-flags.h>')
replace('MODULE_LICENSE("GPL");', 'MODULE_LICENSE("GPL");\n'+(root/'experiments/vmw-balloon-offer.inc').read_text())
replace('static int vmballoon_alloc_page_list(', (root/'experiments/vmw-balloon-targeted.inc').read_text()+'\nstatic int vmballoon_alloc_page_list(')
replace('page = balloon_page_alloc();', 'page = cram_pool_take();')
replace('.page_size = b->max_page_size,', '.page_size = VMW_BALLOON_4K_PAGE,')
replace('\ttarget = READ_ONCE(b->target);', '''\ttarget = READ_ONCE(b->target);
\tif (!target) {
\t\tunsigned long until = READ_ONCE(cram_offer_until);
\t\tif (until && time_before(jiffies, until))
\t\t\ttarget = size + READ_ONCE(cram_pending);
\t}''')
replace('\tpr_debug("%s: failed comm pfn', '\tatomic_long_inc(&cram_refusals);\n\tif (status == VMW_BALLOON_ERROR_PPN_NOTNEEDED)\n\t\tatomic_long_inc(&cram_notneeded);\n\tpr_debug("%s: failed comm pfn')
replace('\t/* format capabilities info */', '''\tseq_printf(f, "lab_refusals: %ld\\n", atomic_long_read(&cram_refusals));
\tseq_printf(f, "lab_notneeded: %ld\\n", atomic_long_read(&cram_notneeded));
\tseq_printf(f, "lab_requested: %lu\\nlab_acquired: %lu\\nlab_failed: %lu\\nlab_pending: %lu\\n",
\t\tREAD_ONCE(cram_requested), READ_ONCE(cram_acquired), READ_ONCE(cram_failed), READ_ONCE(cram_pending));
\tseq_printf(f, "lab_busy: %lu\\nlab_invalid: %lu\\nlab_other: %lu\\nlab_fail_zero_ref: %lu\\nlab_fail_lru: %lu\\nlab_fail_slab: %lu\\nlab_range_success: %lu\\nlab_single_success: %lu\\n",
\t\tcram_busy, cram_invalid, cram_other, cram_fail_zero_ref, cram_fail_lru, cram_fail_slab, cram_range_success, cram_single_success);
\tseq_printf(f, "lab_failed_with_owned_neighbor: %lu\\nlab_blockwise: %u\\nlab_pageblock_pages: %lu\\n", cram_failed_with_owned_neighbor, cram_blockwise, pageblock_nr_pages);
\t/* format capabilities info */''')
replace('\tdebugfs_create_file("vmmemctl",', '\tdebugfs_create_file("cram_target_frames", 0200, NULL, b, &cram_frames_fops);\n\tdebugfs_create_file("vmmemctl",')
replace('\tdebugfs_lookup_and_remove("vmmemctl", NULL);', '\tdebugfs_lookup_and_remove("cram_target_frames", NULL);\n\tdebugfs_lookup_and_remove("vmmemctl", NULL);')
replace('\tvmballoon_pop(&balloon);', '\tvmballoon_pop(&balloon);\n\tcram_pool_free();')
out = root/'private/targeted-build'
out.mkdir(exist_ok=True)
(out/'vmw_balloon.c').write_text(text, encoding='utf-8')
(out/'Makefile').write_text('obj-m += vmw_balloon.o\n', encoding='utf-8')
workload=(root/'experiments/outer-host-residency.py').read_text()
assert workload.count("struct.pack('<Q', i)")==1
(out/'workload.py').write_text(workload.replace("struct.pack('<Q', i)","struct.pack('<Q', i+1)"),encoding='utf-8')
(root/'patches/vmw-balloon-targeted.patch').write_text(''.join(difflib.unified_diff(source.splitlines(True),text.splitlines(True),fromfile='a/drivers/misc/vmw_balloon.c',tofile='b/drivers/misc/vmw_balloon.c')),encoding='utf-8')
