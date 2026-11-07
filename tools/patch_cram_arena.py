"""Apply the opt-in arena experiment to an isolated lab source tree only."""
from pathlib import Path
import argparse,shutil
p=argparse.ArgumentParser();p.add_argument('source',type=Path);a=p.parse_args();root=a.source
assert root.name=='linux-cram-arena','Refuse to alter the existing installed-kernel source'
base=Path(__file__).resolve().parent
for name in ('cram-arena.inc','cram-memcg-reservation.inc'):
 source = base/name if (base/name).exists() else base.parent/'experiments'/name
 shutil.copyfile(source,root/'mm'/name)
def change(name,old,new):
 path=root/name;s=path.read_text();assert s.count(old)==1,(name,old[:90],s.count(old));path.write_text(s.replace(old,new))
def guarded(code):return '\n#ifdef CONFIG_CRAM_ARENA\n'+code+'\n#endif\n'
change('mm/Kconfig','config ZSWAP\n','config CRAM_ARENA\n\tbool "Experimental fixed-arena compressed swap"\n\tdepends on ZSWAP && MEMCG && 64BIT\n\tselect LZ4_COMPRESS\n\tselect LZ4_DECOMPRESS\n\thelp\n\t  Opt-in lab backend with preallocated storage and metadata.\n\nconfig ZSWAP\n')
change('include/linux/memcontrol.h','unsigned long zswap_max;', 'unsigned long zswap_max;'+guarded('\tunsigned long cram_reserved;'))
change('include/linux/memcontrol.h','bool obj_cgroup_may_zswap(struct obj_cgroup *objcg);', 'bool obj_cgroup_may_zswap(struct obj_cgroup *objcg);'+guarded('struct mem_cgroup *cram_memcg_reserve(struct obj_cgroup *objcg, unsigned long bytes);\nvoid cram_memcg_release(struct mem_cgroup *owner, struct obj_cgroup *objcg, unsigned long bytes);'))
change('mm/memcontrol.c','static ssize_t zswap_max_write(',guarded('#include "cram-memcg-reservation.inc"')+'\nstatic ssize_t zswap_max_write(')
change('mm/memcontrol.c','\txchg(&memcg->zswap_max, max);', '''#ifdef CONFIG_CRAM_ARENA
	spin_lock_irq(&cram_budget_lock);
	if (max != PAGE_COUNTER_MAX && memcg->cram_reserved > (max << PAGE_SHIFT)) {
		spin_unlock_irq(&cram_budget_lock);
		return -EBUSY;
	}
	xchg(&memcg->zswap_max, max);
	spin_unlock_irq(&cram_budget_lock);
#else
	xchg(&memcg->zswap_max, max);
#endif''')
change('mm/memcontrol.c','static struct cftype zswap_files[] = {','static struct cftype zswap_files[] = {'+guarded('\t{ .name = "cram.reserved", .flags = CFTYPE_NOT_ON_ROOT, .read_u64 = cram_reserved_read },'))
change('mm/zswap.c','static bool zswap_has_pool;','static bool zswap_has_pool;'+guarded('#include "cram-arena.inc"'))
for signature,code in [
 ('bool zswap_store(struct folio *folio)','if (cram_arena_mb) return ca_store(folio);'),
 ('int zswap_load(struct folio *folio)','if (cram_arena_mb) return ca_load(folio);'),
 ('void zswap_invalidate(swp_entry_t swp)','if (cram_arena_mb) { ca_invalidate(swp); return; }'),
 ('int zswap_swapon(int type, unsigned long nr_pages)','if (cram_arena_mb) return 0;'),
 ('void zswap_swapoff(int type)','if (cram_arena_mb) { ca_swapoff(type); return; }')]:
 change('mm/zswap.c',signature+'\n{',signature+'\n{'+guarded('\t'+code))
change('mm/zswap.c','\t*val = zswap_total_pages() * PAGE_SIZE;','\t*val = zswap_total_pages() * PAGE_SIZE;'+guarded('\tif (cram_arena_mb) *val = ca_bytes;'))
change('mm/zswap.c','\tzswap_debugfs_root = debugfs_create_dir("zswap", NULL);','\tzswap_debugfs_root = debugfs_create_dir("zswap", NULL);'+guarded('\tif (cram_arena_mb) ca_debugfs(zswap_debugfs_root);'))
change('mm/zswap.c','\tzswap_entry_cache = KMEM_CACHE(zswap_entry, 0);',guarded('''	if (cram_arena_mb) {
		ret = ca_setup();
		if (ret) {
			zswap_enabled = false;
			zswap_init_state = ZSWAP_INIT_FAILED;
			return ret;
		}
		zswap_has_pool = true;
		zswap_debugfs_init();
		zswap_init_state = ZSWAP_INIT_SUCCEED;
		return 0;
	}''')+'\n\tzswap_entry_cache = KMEM_CACHE(zswap_entry, 0);')
# Prevent runtime compressor changes from allocating ordinary pools in arena mode.
change('mm/zswap.c','\tmutex_lock(&zswap_init_lock);\n\tswitch (zswap_init_state) {\n\tcase ZSWAP_UNINIT:\n\t\t/* Handled in zswap_setup() */',guarded('\tif (cram_arena_mb) return -EPERM;')+'\n\tmutex_lock(&zswap_init_lock);\n\tswitch (zswap_init_state) {\n\tcase ZSWAP_UNINIT:\n\t\t/* Handled in zswap_setup() */')
print('Applied isolated opt-in arena experiment')
