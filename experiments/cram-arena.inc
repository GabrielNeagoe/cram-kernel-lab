/* SPDX-License-Identifier: GPL-2.0 */
/* Experimental fixed-arena zswap backend. Included by mm/zswap.c. */
#include <linux/bitmap.h>
#include <linux/hash.h>
#include <linux/lz4.h>
#include <linux/debugfs.h>

#define CA_BLOCK 256U
#define CA_MAX_BYTES (4U * 1024U * 1024U)
#define CA_BUCKETS 1024U
static unsigned int cram_arena_mb;
module_param(cram_arena_mb, uint, 0444);
static unsigned int cram_arena_fail_every;
module_param(cram_arena_fail_every, uint, 0444);
static DEFINE_SPINLOCK(ca_lock);
struct ca_entry {
 unsigned long key;
 struct obj_cgroup *objcg;
 struct mem_cgroup *owner;
 u32 next, blocks, length;
};
struct ca_control {
 unsigned long bitmap[BITS_TO_LONGS(CA_MAX_BYTES / CA_BLOCK)];
 u32 heads[CA_BUCKETS];
 unsigned char workspace[LZ4_MEM_COMPRESS] __aligned(8);
 unsigned char output[PAGE_SIZE];
};
static struct ca_control *ca;
static u64 ca_bytes, ca_header_bytes, ca_used, ca_peak, ca_entries;
static u64 ca_stores, ca_loads, ca_full, ca_quota, ca_poor, ca_injected, ca_attempts;
static unsigned int ca_blocks;

static struct ca_entry *ca_at(u32 slot)
{
 return (void *)ca + ca_header_bytes + (slot - 1) * CA_BLOCK;
}
static u32 *ca_find(unsigned long key)
{
 u32 *p = &ca->heads[hash_long(key, 10)];
 while (*p && ca_at(*p)->key != key)
  p = &ca_at(*p)->next;
 return p;
}
/* ca_lock serializes lookup, quota reservation, publication and reclamation. */
static void ca_remove(u32 *link)
{
 u32 slot = *link;
 struct ca_entry *e = ca_at(slot);
 unsigned int blocks = e->blocks;
 *link = e->next;
 cram_memcg_release(e->owner, e->objcg, blocks * CA_BLOCK);
 obj_cgroup_put(e->objcg);
 bitmap_clear(ca->bitmap, slot - 1, blocks);
 ca_used -= blocks * CA_BLOCK;
 ca_entries--;
 atomic_long_dec(&zswap_stored_pages);
}
static void ca_invalidate(swp_entry_t swp)
{
 u32 *link;
 unsigned long flags;
 if (!ca)
  return;
 spin_lock_irqsave(&ca_lock, flags);
 link = ca_find(swp.val);
 if (*link)
  ca_remove(link);
 spin_unlock_irqrestore(&ca_lock, flags);
}
static bool ca_store(struct folio *folio)
{
 struct obj_cgroup *objcg;
 struct mem_cgroup *owner;
 struct ca_entry *e;
 unsigned int blocks, idx;
 int length;
 void *input;
 u32 *link;
 long i;
 unsigned long flags;
 bool ok = false;
 /* Reject large folios and remove every stale partial entry before disk IO. */
 if (!ca || !zswap_enabled || folio_test_large(folio)) {
  for (i = 0; i < folio_nr_pages(folio); i++)
   ca_invalidate(swp_entry(swp_type(folio->swap), swp_offset(folio->swap) + i));
  return false;
 }
 objcg = get_obj_cgroup_from_folio(folio);
 if (!objcg) {
  ca_invalidate(folio->swap);
  return false;
 }
 spin_lock_irqsave(&ca_lock, flags);
 link = ca_find(folio->swap.val);
 if (*link)
  ca_remove(link);
 input = kmap_local_folio(folio, 0);
 length = LZ4_compress_default(input, ca->output, PAGE_SIZE, PAGE_SIZE - 1,
                               ca->workspace);
 kunmap_local(input);
 if (length <= 0) {
  ca_poor++;
  goto out;
 }
 blocks = DIV_ROUND_UP(sizeof(*e) + length, CA_BLOCK);
 idx = bitmap_find_next_zero_area(ca->bitmap, ca_blocks, 0, blocks, 0);
 if (idx >= ca_blocks) {
  ca_full++;
  goto out;
 }
 /* Reserve the exact rounded slot footprint in every ancestor before use. */
 owner = cram_memcg_reserve(objcg, blocks * CA_BLOCK);
 if (IS_ERR(owner)) {
  ca_quota++;
  goto out;
 }
 ca_attempts++;
 if (cram_arena_fail_every && ca_attempts % cram_arena_fail_every == 0) {
  cram_memcg_release(owner, objcg, blocks * CA_BLOCK);
  ca_injected++;
  goto out;
 }
 bitmap_set(ca->bitmap, idx, blocks);
 e = ca_at(idx + 1);
 e->key = folio->swap.val;
 e->objcg = objcg;
 e->owner = owner;
 e->blocks = blocks;
 e->length = length;
 memcpy(e + 1, ca->output, length);
 link = ca_find(e->key);
 e->next = *link;
 *link = idx + 1;
 ca_used += blocks * CA_BLOCK;
 ca_peak = max(ca_peak, ca_used);
 ca_entries++;
 ca_stores++;
 atomic_long_inc(&zswap_stored_pages);
 count_objcg_events(objcg, ZSWPOUT, 1);
 count_vm_event(ZSWPOUT);
 WARN_ON_ONCE(ca_header_bytes + ca_used > ca_bytes);
 ok = true;
out:
 spin_unlock_irqrestore(&ca_lock, flags);
 if (!ok)
  obj_cgroup_put(objcg);
 return ok;
}
static int ca_load(struct folio *folio)
{
 u32 *link;
 struct ca_entry *e;
 void *output;
 int length;
 unsigned long flags;
 if (!ca)
  return -ENOENT;
 if (folio_test_large(folio)) {
  folio_unlock(folio);
  return -EINVAL;
 }
 spin_lock_irqsave(&ca_lock, flags);
 link = ca_find(folio->swap.val);
 if (!*link) {
  spin_unlock_irqrestore(&ca_lock, flags);
  return -ENOENT;
 }
 e = ca_at(*link);
 output = kmap_local_folio(folio, 0);
 length = LZ4_decompress_safe((void *)(e + 1), output, e->length, PAGE_SIZE);
 kunmap_local(output);
 if (length != PAGE_SIZE) {
  spin_unlock_irqrestore(&ca_lock, flags);
  folio_unlock(folio);
  return -EIO;
 }
 flush_dcache_folio(folio);
 folio_mark_uptodate(folio);
 folio_mark_dirty(folio);
 count_objcg_events(e->objcg, ZSWPIN, 1);
 count_vm_event(ZSWPIN);
 ca_loads++;
 ca_remove(link);
 spin_unlock_irqrestore(&ca_lock, flags);
 folio_unlock(folio);
 return 0;
}
static void ca_swapoff(int type)
{
 unsigned int i;
 unsigned long flags;
 if (!ca)
  return;
 spin_lock_irqsave(&ca_lock, flags);
 for (i = 0; i < CA_BUCKETS; i++) {
  u32 *p = &ca->heads[i];
  while (*p) {
   if (swp_type((swp_entry_t){.val = ca_at(*p)->key}) == type)
    ca_remove(p);
   else
    p = &ca_at(*p)->next;
  }
 }
 spin_unlock_irqrestore(&ca_lock, flags);
}
static int ca_setup(void)
{
 unsigned long bytes = (unsigned long)cram_arena_mb << 20;
 if (PAGE_SIZE != 4096 || !is_power_of_2(bytes) || bytes > CA_MAX_BYTES ||
     bytes < (1U << 20) || get_order(bytes) > MAX_PAGE_ORDER)
  return -EINVAL;
 ca = alloc_pages_exact(bytes, GFP_KERNEL | __GFP_ZERO);
 if (!ca)
  return -ENOMEM;
 ca_bytes = bytes;
 ca_header_bytes = ALIGN(sizeof(*ca), CA_BLOCK);
 ca_blocks = (bytes - ca_header_bytes) / CA_BLOCK;
 pr_info("CRAM arena: backing=%llu overhead=%llu capacity=%u block=%u codec=lz4\n",
         ca_bytes, ca_header_bytes, ca_blocks * CA_BLOCK, CA_BLOCK);
 return 0;
}
static void ca_debugfs(struct dentry *root)
{
#define CA_STAT(name, variable) debugfs_create_u64(name, 0444, root, &variable)
 CA_STAT("arena_backing_bytes", ca_bytes);
 CA_STAT("arena_overhead_bytes", ca_header_bytes);
 CA_STAT("arena_used_bytes", ca_used);
 CA_STAT("arena_peak_bytes", ca_peak);
 CA_STAT("arena_entries", ca_entries);
 CA_STAT("arena_stores", ca_stores);
 CA_STAT("arena_loads", ca_loads);
 CA_STAT("arena_reject_full", ca_full);
 CA_STAT("arena_reject_quota", ca_quota);
 CA_STAT("arena_reject_poor", ca_poor);
 CA_STAT("arena_injected_failures", ca_injected);
#undef CA_STAT
}
