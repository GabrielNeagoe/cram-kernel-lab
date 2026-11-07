/* SPDX-License-Identifier: GPL-2.0-or-later
 * Experimental trapped-access compressed backing store. Requires the BQL.
 * No migration or hot-unplug support is claimed by this prototype.
 */
#include <zstd.h>
#include "migration/blocker.h"
#define STORE_PAGE 4096
#define STORE_CACHE 64
typedef struct StoreBlock {
    uint8_t *data;
    uint32_t length;
} StoreBlock;
typedef struct StoreCache {
    uint8_t data[STORE_PAGE];
    uint64_t page, age;
    bool valid, dirty;
} StoreCache;
typedef struct CT3Store {
    StoreBlock *blocks;
    StoreCache cache[STORE_CACHE];
    ZSTD_CCtx *context;
    uint64_t pages, clock;
    Error *migration_blocker;
} CT3Store;

static bool store_flush(CXLCT3State *s, StoreCache *cache)
{
    CT3Store *store = s->store;
    StoreBlock *block = &store->blocks[cache->page];
    uint8_t packed[8192];
    uint8_t *replacement;
    size_t length;
    if (!cache->valid || !cache->dirty) {
        return true;
    }
    length = ZSTD_compressCCtx(store->context, packed, sizeof(packed),
                               cache->data, STORE_PAGE, 1);
    if (ZSTD_isError(length)) {
        return false;
    }
    length = MIN(length, STORE_PAGE);
    replacement = g_try_malloc(length);
    if (!replacement) {
        return false;
    }
    memcpy(replacement, length == STORE_PAGE ? cache->data : packed, length);
    s->store_payload_bytes -= block->length;
    g_free(block->data);
    block->data = replacement;
    block->length = length;
    s->store_payload_bytes += length;
    cache->dirty = false;
    return true;
}

static StoreCache *store_get(CXLCT3State *s, uint64_t page)
{
    CT3Store *store = s->store;
    StoreCache *victim = &store->cache[0];
    StoreBlock *block;
    unsigned int i;
    if (page >= store->pages) {
        return NULL;
    }
    for (i = 0; i < STORE_CACHE; i++) {
        StoreCache *cache = &store->cache[i];
        if (cache->valid && cache->page == page) {
            cache->age = ++store->clock;
            return cache;
        }
        if (!cache->valid || (victim->valid && cache->age < victim->age)) {
            victim = cache;
        }
    }
    if (!store_flush(s, victim)) {
        return NULL;
    }
    block = &store->blocks[page];
    /* An invalid cache line must never expose a partial decode. */
    victim->valid = false;
    if (!block->data) {
        memset(victim->data, 0, STORE_PAGE);
    } else if (block->length == STORE_PAGE) {
        memcpy(victim->data, block->data, STORE_PAGE);
    } else if (ZSTD_decompress(victim->data, STORE_PAGE,
                               block->data, block->length) != STORE_PAGE) {
        return NULL;
    }
    victim->page = page;
    victim->valid = true;
    victim->dirty = false;
    victim->age = ++store->clock;
    s->store_cache_misses++;
    return victim;
}

static MemTxResult store_read(void *opaque, hwaddr address, uint64_t *value,
                              unsigned int size, MemTxAttrs attrs)
{
    CXLCT3State *s = opaque;
    StoreCache *cache = NULL;
    unsigned int i;
    *value = 0;
    if (address >= s->vmem_size || size > s->vmem_size - address) {
        return MEMTX_ERROR;
    }
    for (i = 0; i < size; i++) {
        if (!cache || (address + i) % STORE_PAGE == 0) {
            cache = store_get(s, (address + i) / STORE_PAGE);
        }
        if (!cache) {
            return MEMTX_ERROR;
        }
        *value |= (uint64_t)cache->data[(address + i) % STORE_PAGE] << (8 * i);
    }
    s->store_read_ops++;
    return MEMTX_OK;
}

static MemTxResult store_write(void *opaque, hwaddr address, uint64_t value,
                               unsigned int size, MemTxAttrs attrs)
{
    CXLCT3State *s = opaque;
    StoreCache *cache = NULL;
    unsigned int i;
    if (address >= s->vmem_size || size > s->vmem_size - address) {
        return MEMTX_ERROR;
    }
    for (i = 0; i < size; i++) {
        if (!cache || (address + i) % STORE_PAGE == 0) {
            cache = store_get(s, (address + i) / STORE_PAGE);
        }
        if (!cache) {
            return MEMTX_ERROR;
        }
        cache->data[(address + i) % STORE_PAGE] = value >> (8 * i);
        cache->dirty = true;
    }
    s->store_write_ops++;
    return MEMTX_OK;
}

static const MemoryRegionOps store_ops = {
    .read_with_attrs = store_read,
    .write_with_attrs = store_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static bool store_init(CXLCT3State *s, Error **errp)
{
    CT3Store *store;
    if (!s->vmem_size || s->vmem_size % STORE_PAGE || s->vmem_size > 256 * MiB) {
        error_setg(errp, "Experimental compressed store requires at most 256 MiB, page aligned");
        return false;
    }
    store = g_try_new0(CT3Store, 1);
    if (!store) {
        error_setg(errp, "Cannot allocate compressed store cache");
        return false;
    }
    store->pages = s->vmem_size / STORE_PAGE;
    store->blocks = g_try_new0(StoreBlock, store->pages);
    store->context = ZSTD_createCCtx();
    if (!store->blocks || !store->context) {
        g_free(store->blocks);
        ZSTD_freeCCtx(store->context);
        g_free(store);
        error_setg(errp, "Cannot allocate compressed store metadata");
        return false;
    }
    error_setg(&store->migration_blocker,
               "Experimental compressed CXL store has no migration format");
    if (migrate_add_blocker(&store->migration_blocker, errp) < 0) {
        g_free(store->blocks);
        ZSTD_freeCCtx(store->context);
        g_free(store);
        return false;
    }
    s->store = store;
    memory_region_init_io(&s->store_mr, OBJECT(s), &store_ops, s,
                         "ct3-compressed-store", s->vmem_size);
    object_property_add_uint64_ptr(OBJECT(s), "store-payload-bytes",
                                  &s->store_payload_bytes, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(OBJECT(s), "store-cache-misses",
                                  &s->store_cache_misses, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(OBJECT(s), "store-read-ops",
                                  &s->store_read_ops, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(OBJECT(s), "store-write-ops",
                                  &s->store_write_ops, OBJ_PROP_FLAG_READ);
    return true;
}

static void store_destroy(CXLCT3State *s)
{
    CT3Store *store = s->store;
    uint64_t i;
    if (!store) {
        return;
    }
    object_unparent(OBJECT(&s->store_mr));
    migrate_del_blocker(&store->migration_blocker);
    for (i = 0; i < store->pages; i++) {
        g_free(store->blocks[i].data);
    }
    g_free(store->blocks);
    ZSTD_freeCCtx(store->context);
    g_free(store);
    s->store = NULL;
}
