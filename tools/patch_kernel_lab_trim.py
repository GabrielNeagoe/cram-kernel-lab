"""Add opt-in mode 4 to the lab kernel, preserving each original source file."""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1])
fragment = Path(sys.argv[2])
def edit(name, transform):
    p = root/name
    backup = p.with_name(p.name+'.before-lab-trim')
    assert not backup.exists(), backup
    old = p.read_text(); new = transform(old)
    assert old != new
    shutil.copyfile(p, backup); p.write_text(new)

edit('drivers/cxl/core/region.c', lambda s: s.replace('#include <linux/memregion.h>',
     '#include <linux/memregion.h>\n#include <linux/dmi.h>\n#include <linux/pci.h>\n#ifdef CONFIG_X86\n#include <asm/cacheflush.h>\n#endif').replace(
     'static int cxl_region_debugfs_poison_inject(', '#include "cxl-cram-lab-trim.inc"\n\nstatic int cxl_region_debugfs_poison_inject('))
shutil.copyfile(fragment, root/'drivers/cxl/core/cxl-cram-lab-trim.inc')
edit('drivers/cxl/cxl.h', lambda s:s[:s.rfind('#endif')] +
     'int cxl_region_cram_lab_trim(struct cxl_region *cxlr, unsigned long pfn);\n' + s[s.rfind('#endif'):])
edit('drivers/dax/dax-private.h', lambda s:s.replace('struct dax_region {',
     'struct dax_region {\n\tint (*cram_lab_trim)(struct device *dev, unsigned long pfn);'))
edit('drivers/dax/cxl.c', lambda s:s.replace('#include "bus.h"', '''#include "bus.h"
#include "dax-private.h"
static int dax_cxl_cram_lab_trim(struct device *dev, unsigned long pfn)
{
    return cxl_region_cram_lab_trim(to_cxl_dax_region(dev)->cxlr, pfn);
}
''').replace('\tdata = (struct dev_dax_data) {',
     '\tdax_region->cram_lab_trim = dax_cxl_cram_lab_trim;\n\n\tdata = (struct dev_dax_data) {'))
def kmem(s):
    s=s.replace('struct dax_kmem_data {', 'struct dax_kmem_data {\n\tstruct dev_dax *dev_dax;')
    s=s.replace('\tdata->res_name = kstrdup(', '\tdata->dev_dax = dev_dax;\n\tdata->res_name = kstrdup(')
    s=s.replace('mode > 3)', 'mode > 4)')
    s=s.replace('\tcase 2:\n', '''	case 4: {
		struct dax_region *region = data->dev_dax->region;
		bool failed = false;
		for (i = 0; i < nr_pages; i++) {
			result[i] = region->cram_lab_trim ?
				region->cram_lab_trim(region->dev, pfns[i]) : -EOPNOTSUPP;
			if (result[i])
				failed = true;
			else
				data->cram_trim_pages++;
		}
		return failed ? -EBUSY : 0;
	}
	case 2:
''',1)
    return s
edit('drivers/dax/kmem.c', kmem)
print('Lab-only trim mode 4 source prepared; default remains mode 1')
