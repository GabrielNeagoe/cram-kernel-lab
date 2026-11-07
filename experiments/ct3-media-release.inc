/* SPDX-License-Identifier: GPL-2.0-or-later */
static bool store_fill(CXLCT3State *s, uint64_t address, uint64_t length,
                       uint8_t value)
{
    if (address > s->vmem_size || length > s->vmem_size - address) {
        return false;
    }
    while (length) {
        StoreCache *cache = store_get(s, address / STORE_PAGE);
        size_t offset = address % STORE_PAGE;
        size_t chunk = MIN(length, STORE_PAGE - offset);
        if (!cache) {
            return false;
        }
        memset(cache->data + offset, value, chunk);
        cache->dirty = true;
        address += chunk;
        length -= chunk;
    }
    return true;
}

static CXLRetCode cmd_ct3_media_operations(const struct cxl_cmd *cmd,
                                         uint8_t *payload_in, size_t len_in,
                                         uint8_t *payload_out, size_t *len_out,
                                         CXLCCI *cci)
{
    CXLCT3State *s = CXL_CT3(cci->d);
    CXLType3Dev *ct3d = &s->ct3d;
    MemTxAttrs attrs = {};
    uint32_t count;
    uint64_t capacity = 0;
    uint8_t fill;

    *len_out = 0;
    if (len_in < 8) {
        return CXL_MBOX_INVALID_PAYLOAD_LENGTH;
    }
    if (payload_in[0] != 1 || payload_in[1] > 1) {
        return CXL_MBOX_UNSUPPORTED;
    }
    fill = payload_in[1] == 1 ? 0 : 0x0f;
    count = ldl_le_p(payload_in + 4);
    if (count > (len_in - 8) / 16) {
        return CXL_MBOX_INVALID_PAYLOAD_LENGTH;
    }
    if (ct3d->hostvmem) {
        capacity = memory_region_size(
            host_memory_backend_get_memory(ct3d->hostvmem));
    }
    /* Validate the complete batch before changing even its first range. */
    for (uint32_t i = 0; i < count; i++) {
        uint64_t dpa = ldq_le_p(payload_in + 8 + (size_t)i * 16);
        uint64_t length = ldq_le_p(payload_in + 16 + (size_t)i * 16);
        if (dpa > capacity || length > capacity - dpa) {
            return CXL_MBOX_INVALID_PA;
        }
    }
    for (uint32_t i = 0; i < count; i++) {
        uint64_t dpa = ldq_le_p(payload_in + 8 + (size_t)i * 16);
        uint64_t length = ldq_le_p(payload_in + 16 + (size_t)i * 16);
        if (!length) {
            continue;
        }
        if (s->store && !fill && !(dpa % STORE_PAGE) &&
            !(length % STORE_PAGE)) {
            if (!store_release(s, dpa, length)) {
                return CXL_MBOX_INTERNAL_ERROR;
            }
        } else if (s->store) {
            /* Internal mailbox writes must not reenter the device's IO MR. */
            if (!store_fill(s, dpa, length, fill)) {
                return CXL_MBOX_INTERNAL_ERROR;
            }
        } else if (address_space_set(&ct3d->hostvmem_as, dpa, fill,
                                     length, attrs) != MEMTX_OK) {
            /* Earlier ranges may have completed; never report false success. */
            return CXL_MBOX_INTERNAL_ERROR;
        }
    }
    return CXL_MBOX_SUCCESS;
}
