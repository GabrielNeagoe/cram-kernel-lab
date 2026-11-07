/* SPDX-License-Identifier: GPL-2.0-or-later
 * Device-side primitive only: caller must own the range and hold the BQL.
 * Deliberately not wired to the kernel's simulated trim callback.
 */
static bool store_release(CXLCT3State *s, uint64_t address, uint64_t length)
{
    CT3Store *store = s->store;
    uint64_t first, count, i;

    /* Subtraction avoids wrapping address + length. Reject before mutation. */
    if (!store || address > s->vmem_size ||
        length > s->vmem_size - address ||
        address % STORE_PAGE || length % STORE_PAGE) {
        return false;
    }
    first = address / STORE_PAGE;
    count = length / STORE_PAGE;
    if (first > store->pages || count > store->pages - first) {
        return false;
    }
    for (i = 0; i < STORE_CACHE; i++) {
        StoreCache *cache = &store->cache[i];
        if (cache->valid && cache->page >= first &&
            cache->page - first < count) {
            /* Never flush discarded dirty bytes back into the store. */
            cache->valid = false;
            cache->dirty = false;
        }
    }
    for (i = first; i < first + count; i++) {
        StoreBlock *block = &store->blocks[i];
        s->store_payload_bytes -= block->length;
        g_free(block->data);
        block->data = NULL;
        block->length = 0;
    }
    return true;
}
