/* SPDX-License-Identifier: GPL-2.0 */
/* Reservation counters are independent of batched zswap accounting. */
static DEFINE_SPINLOCK(cram_budget_lock);
struct mem_cgroup *cram_memcg_reserve(struct obj_cgroup *objcg, unsigned long bytes)
{
 struct mem_cgroup *owner = get_mem_cgroup_from_objcg(objcg), *m;
 unsigned long flags;
 bool allowed = true;
 if (mem_cgroup_is_root(owner)) {
  mem_cgroup_put(owner);
  return ERR_PTR(-EPERM);
 }
 spin_lock_irqsave(&cram_budget_lock, flags);
 for (m = owner; m; m = parent_mem_cgroup(m)) {
  unsigned long limit = READ_ONCE(m->zswap_max);
  if (limit != PAGE_COUNTER_MAX &&
      (bytes > (limit << PAGE_SHIFT) || m->cram_reserved > (limit << PAGE_SHIFT) - bytes)) {
   allowed = false;
   break;
  }
 }
 if (allowed)
  for (m = owner; m; m = parent_mem_cgroup(m))
   m->cram_reserved += bytes;
 spin_unlock_irqrestore(&cram_budget_lock, flags);
 if (!allowed) {
  mem_cgroup_put(owner);
  return ERR_PTR(-ENOSPC);
 }
 /* NOWAIT: never enter direct reclaim while the arena spinlock is held. */
 if (obj_cgroup_charge(objcg, GFP_NOWAIT | __GFP_NOWARN, bytes)) {
  spin_lock_irqsave(&cram_budget_lock, flags);
  for (m = owner; m; m = parent_mem_cgroup(m))
   m->cram_reserved -= bytes;
  spin_unlock_irqrestore(&cram_budget_lock, flags);
  mem_cgroup_put(owner);
  return ERR_PTR(-ENOMEM);
 }
 rcu_read_lock();
 m = obj_cgroup_memcg(objcg);
 mod_memcg_state(m, MEMCG_ZSWAP_B, bytes);
 mod_memcg_state(m, MEMCG_ZSWAPPED, 1);
 rcu_read_unlock();
 return owner;
}
void cram_memcg_release(struct mem_cgroup *owner, struct obj_cgroup *objcg,
                        unsigned long bytes)
{
 struct mem_cgroup *m;
 unsigned long flags;
 obj_cgroup_uncharge(objcg, bytes);
 rcu_read_lock();
 m = obj_cgroup_memcg(objcg);
 mod_memcg_state(m, MEMCG_ZSWAP_B, -bytes);
 mod_memcg_state(m, MEMCG_ZSWAPPED, -1);
 rcu_read_unlock();
 spin_lock_irqsave(&cram_budget_lock, flags);
 for (m = owner; m; m = parent_mem_cgroup(m)) {
  WARN_ON_ONCE(m->cram_reserved < bytes);
  m->cram_reserved -= bytes;
 }
 spin_unlock_irqrestore(&cram_budget_lock, flags);
 mem_cgroup_put(owner);
}
static u64 cram_reserved_read(struct cgroup_subsys_state *css, struct cftype *cft)
{
 unsigned long flags, value;
 spin_lock_irqsave(&cram_budget_lock, flags);
 value = mem_cgroup_from_css(css)->cram_reserved;
 spin_unlock_irqrestore(&cram_budget_lock, flags);
 return value;
}
