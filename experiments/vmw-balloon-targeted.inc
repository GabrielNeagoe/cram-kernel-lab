/* Bounded lab-only acquisition of caller-supplied frames via the allocator.
 * Binary input is a strictly increasing array of <=8192 native-endian u64 PFNs.
 * No frame is offered until alloc_contig_range has established ownership.
 */
static DEFINE_MUTEX(cram_pool_lock);
static LIST_HEAD(cram_pool);
static unsigned long cram_pending;
static unsigned long cram_requested, cram_acquired, cram_failed;
static unsigned long cram_busy, cram_invalid, cram_other;
static unsigned long cram_fail_zero_ref, cram_fail_lru, cram_fail_slab;
static unsigned long cram_range_success, cram_single_success;
static bool cram_blockwise;
module_param(cram_blockwise, bool, 0444);
static unsigned long cram_failed_with_owned_neighbor;

static bool cram_owned_neighbor(unsigned long pfn)
{
	struct page *page;
	list_for_each_entry(page, &cram_pool, lru)
		if (page_to_pfn(page) / pageblock_nr_pages == pfn / pageblock_nr_pages)
			return true;
	return false;
}

static void cram_pool_free(void)
{
	struct page *page, *next;

	mutex_lock(&cram_pool_lock);
	list_for_each_entry_safe(page, next, &cram_pool, lru) {
		list_del(&page->lru);
		__free_page(page);
	}
	cram_pending = 0;
	mutex_unlock(&cram_pool_lock);
}

static struct page *cram_pool_take(void)
{
	struct page *page = NULL;

	mutex_lock(&cram_pool_lock);
	if (!list_empty(&cram_pool)) {
		page = list_first_entry(&cram_pool, struct page, lru);
		list_del(&page->lru);
		cram_pending--;
	}
	mutex_unlock(&cram_pool_lock);
	return page;
}

static ssize_t cram_frames_write(struct file *file, const char __user *buf,
				size_t len, loff_t *pos)
{
	struct vmballoon *b = file->private_data;
	u64 *pfns;
	size_t i, j, n = len / sizeof(u64);
	int ret = 0;

	if (!capable(CAP_SYS_ADMIN) || !len || len % sizeof(u64) || n > 8192)
		return -EINVAL;
	pfns = memdup_user(buf, len);
	if (IS_ERR(pfns))
		return PTR_ERR(pfns);
	for (i = 0; i < n; i++) {
		if (pfns[i] > ULONG_MAX || !pfn_valid(pfns[i]) ||
		    PageReserved(pfn_to_page(pfns[i])) ||
		    (i && pfns[i] <= pfns[i-1])) {
			ret = -EINVAL;
			goto out;
		}
	}
	down_write(&b->conf_sem);
	if (atomic64_read(&b->size) || b->target || READ_ONCE(cram_offer_until)) {
		ret = -EBUSY;
		goto unlock;
	}
	cram_pool_free();
	cram_requested = n;
	cram_acquired = cram_failed = 0;
	cram_busy = cram_invalid = cram_other = 0;
	cram_fail_zero_ref = cram_fail_lru = cram_fail_slab = 0;
	cram_range_success = cram_single_success = 0;
	cram_failed_with_owned_neighbor = 0;
	mutex_lock(&cram_pool_lock);
	for (i = 0; i < n; i = j) {
		for (j = i + 1; j < n &&
		     (cram_blockwise ? pfns[j] / pageblock_nr_pages == pfns[i] / pageblock_nr_pages :
		      pfns[j] == pfns[j-1] + 1) &&
		     page_zone(pfn_to_page(pfns[j])) == page_zone(pfn_to_page(pfns[i])); j++)
			;
		ret = alloc_contig_range(pfns[i], pfns[j-1] + 1, ACR_FLAGS_NONE,
					GFP_KERNEL | __GFP_NOWARN);
		if (ret) {
			/* A busy frame must not exclude its available neighbours. */
			for (; i < j; i++) {
				ret = alloc_contig_range(pfns[i], pfns[i] + 1,
					ACR_FLAGS_NONE, GFP_KERNEL | __GFP_NOWARN);
				if (ret) {
					cram_failed++;
					cram_busy += ret == -EBUSY;
					cram_invalid += ret == -EINVAL;
					cram_other += ret != -EBUSY && ret != -EINVAL;
					cram_fail_zero_ref += !page_count(pfn_to_page(pfns[i]));
					cram_fail_lru += PageLRU(pfn_to_page(pfns[i]));
					cram_fail_slab += PageSlab(pfn_to_page(pfns[i]));
					cram_failed_with_owned_neighbor += cram_owned_neighbor(pfns[i]);
					continue;
				}
				list_add_tail(&pfn_to_page(pfns[i])->lru, &cram_pool);
				cram_acquired++;
				cram_single_success++;
				cram_pending++;
			}
			continue;
		}
		{
			unsigned long pfn, end = pfns[j-1] + 1;
			/* At most one pageblock of gaps is temporarily acquired.
			 * Free every non-requested frame through the allocator. */
			for (pfn = pfns[i]; pfn < end; pfn++) {
				if (i < j && pfn == pfns[i]) {
					list_add_tail(&pfn_to_page(pfn)->lru, &cram_pool);
					cram_acquired++;
					cram_range_success++;
					cram_pending++;
					i++;
				} else {
					__free_page(pfn_to_page(pfn));
				}
			}
		}
	}
	mutex_unlock(&cram_pool_lock);
	ret = 0;
unlock:
	up_write(&b->conf_sem);
out:
	kfree(pfns);
	return ret ? ret : len;
}

static const struct file_operations cram_frames_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.write = cram_frames_write,
};
