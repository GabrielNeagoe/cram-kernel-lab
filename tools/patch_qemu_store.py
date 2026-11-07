"""Apply the experimental store only in the isolated QEMU checkout."""
from pathlib import Path
root=Path('/srv/cram-lab/qemu-compressed')
def replace(path,old,new):
    p=root/path;s=p.read_text();assert s.count(old)==1,(path,old)
    p.write_text(s.replace(old,new))
replace('hw/cxl/compressed_type3/ct3.h','    uint32_t ct3_ratio;', '''    uint32_t ct3_ratio;
    bool compress_store;
    void *store;
    MemoryRegion store_mr;
    uint64_t store_payload_bytes, store_cache_misses;
    uint64_t store_read_ops, store_write_ops;''')
replace('include/hw/cxl/cxl_device.h','    AddressSpace hostvmem_as;',
        '    AddressSpace hostvmem_as;\n    MemoryRegion *experimental_vmem_override;')
replace('hw/cxl/cxl-host.c',
        '        MemoryRegion *vmr = host_memory_backend_get_memory(ct3d->hostvmem);',
        '''        MemoryRegion *vmr = ct3d->experimental_vmem_override ?
            ct3d->experimental_vmem_override :
            host_memory_backend_get_memory(ct3d->hostvmem);''')
replace('hw/cxl/compressed_type3/ct3.c','#include "ct3.h"',
        '#include "ct3.h"\n#include "qemu/units.h"\n#include "ct3-store.inc"')
replace('hw/cxl/compressed_type3/ct3.c',
        '    DEFINE_PROP_UINT32("ct3-ratio", CXLCT3State, ct3_ratio, ~(0)),',
        '''    DEFINE_PROP_UINT32("ct3-ratio", CXLCT3State, ct3_ratio, ~(0)),
    DEFINE_PROP_BOOL("ct3-compress", CXLCT3State, compress_store, false),''')
replace('hw/cxl/compressed_type3/ct3.c',
        '''    /* Let the parent type3 device set up everything (incl. MSI-X) */
    ct3_realize(pci_dev, errp);
    if (*errp) {
        return;
    }

    mr = host_memory_backend_get_memory(s->ct3d.hostvmem);
    s->vmem_size = memory_region_size(mr);''',
        '''    mr = host_memory_backend_get_memory(s->ct3d.hostvmem);
    s->vmem_size = memory_region_size(mr);
    /* Reject unsupported configurations before parent device setup. */
    if (s->compress_store && !store_init(s, errp)) {
        return;
    }
    /* Let the parent type3 device set up everything (incl. MSI-X). */
    ct3_realize(pci_dev, errp);
    if (*errp) {
        store_destroy(s);
        return;
    }
    if (s->compress_store) {
        address_space_destroy(&s->ct3d.hostvmem_as);
        address_space_init(&s->ct3d.hostvmem_as, &s->store_mr,
                           "ct3-compressed-vmem");
        s->ct3d.experimental_vmem_override = &s->store_mr;
    }''')
replace('hw/cxl/compressed_type3/ct3.c',
        '''static void cxl_ct3_exit(PCIDevice *pci_dev)
{
    ct3_exit(pci_dev);
}''',
        '''static void cxl_ct3_exit(PCIDevice *pci_dev)
{
    CXLCT3State *s = CXL_CT3(pci_dev);
    ct3_exit(pci_dev);
    store_destroy(s);
}''')
print('Experimental store patch applied')
