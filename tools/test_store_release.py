"""Compile actual store core + proposed release helper with ASan/UBSan.

Run on Linux with a C compiler and libzstd development headers. QEMU object
lifecycle is excluded; storage/compression/cache code is taken verbatim.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
core = (root / 'experiments/ct3-store.inc').read_text()
core = core.replace('#include "migration/blocker.h"', '')
core = core[:core.index('static const MemoryRegionOps store_ops')]
release = (root / 'experiments/ct3-store-release.inc').read_text()
prefix = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
typedef void Error;
typedef uint64_t hwaddr;
typedef int MemTxAttrs;
typedef int MemTxResult;
#define MEMTX_ERROR 1
#define MEMTX_OK 0
#define MIN(a,b) ((a)<(b)?(a):(b))
#define g_try_malloc malloc
#define g_free free
typedef struct CXLCT3State {
    struct CT3Store *store;
    uint64_t vmem_size, store_payload_bytes, store_cache_misses;
    uint64_t store_read_ops, store_write_ops;
} CXLCT3State;
'''
tests = r'''
static uint8_t expected[128][STORE_PAGE];
static void check_page(CXLCT3State *s, unsigned page) {
    for (unsigned j = 0; j < STORE_PAGE; j += 8) {
        uint64_t got, want = 0;
        for (unsigned k = 0; k < 8; k++)
            want |= (uint64_t)expected[page][j+k] << (8*k);
        assert(store_read(s, page*STORE_PAGE+j, &got, 8, 0) == MEMTX_OK);
        assert(got == want);
    }
}
static void check_accounting(CXLCT3State *s) {
    uint64_t sum = 0;
    for (unsigned p = 0; p < 128; p++) sum += s->store->blocks[p].length;
    assert(sum == s->store_payload_bytes);
}
int main(void) {
    CT3Store store = {0};
    CXLCT3State s = {.store=&store, .vmem_size=128*STORE_PAGE};
    uint64_t rng = 123456789;
    store.pages = 128;
    store.blocks = calloc(store.pages, sizeof(StoreBlock));
    store.context = ZSTD_createCCtx();
    assert(store.blocks && store.context);
    for (unsigned p=0; p<128; p++) {
        for (unsigned j=0; j<STORE_PAGE; j++) {
            rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
            expected[p][j] = p%2 ? (uint8_t)rng : 0xab;
            assert(store_write(&s,p*STORE_PAGE+j,expected[p][j],1,0)==MEMTX_OK);
        }
    }
    /* Older pages include compressed and raw storage; recent ones are dirty. */
    assert(store.blocks[0].length > 0 && store.blocks[0].length < STORE_PAGE);
    assert(store.blocks[1].length == STORE_PAGE);
    assert(store.cache[0].valid);
    uint64_t before = s.store_payload_bytes;
    assert(!store_release(&s, UINT64_MAX-4095, 8192));
    assert(!store_release(&s, 0, UINT64_MAX-4095));
    assert(!store_release(&s, 1, STORE_PAGE));
    assert(!store_release(&s, 0, STORE_PAGE-1));
    assert(!store_release(&s, s.vmem_size, STORE_PAGE));
    assert(s.store_payload_bytes == before);
    for (unsigned p=0; p<128; p++) check_page(&s,p);
    puts("PASS invalid ranges preserve all data");
    /* Make a cached page dirty over an existing stored representation. */
    assert(store_write(&s, 70*STORE_PAGE, 0x42, 1, 0)==MEMTX_OK);
    expected[70][0]=0x42;
    assert(store_release(&s, 10*STORE_PAGE, 100*STORE_PAGE));
    memset(expected[10], 0, 100*STORE_PAGE);
    check_accounting(&s);
    for (unsigned p=10; p<110; p++) {
        assert(!store.blocks[p].data && !store.blocks[p].length);
    }
    for (unsigned p=0; p<128; p++) check_page(&s,p);
    puts("PASS compressed/raw/dirty release, zero reads, live neighbors");
    before=s.store_payload_bytes;
    assert(store_release(&s,10*STORE_PAGE,100*STORE_PAGE));
    assert(store_release(&s,s.vmem_size,0));
    assert(s.store_payload_bytes==before);
    puts("PASS idempotence and empty range at end");
    for (unsigned p=10; p<110; p++) {
        expected[p][0]=(uint8_t)p;
        assert(store_write(&s,p*STORE_PAGE,p,1,0)==MEMTX_OK);
    }
    for (unsigned p=0; p<128; p++) check_page(&s,p);
    check_accounting(&s);
    puts("PASS reuse across cache evictions and payload accounting");
    assert(store_release(&s,0,s.vmem_size));
    assert(!s.store_payload_bytes);
    memset(expected,0,sizeof(expected));
    for (unsigned p=0; p<128; p++) check_page(&s,p);
    free(store.blocks);
    ZSTD_freeCCtx(store.context);
    puts("PASS full-store release; all checks complete");
}
'''
with tempfile.TemporaryDirectory(prefix='cram-release-test-') as tmp:
    source = Path(tmp) / 'test.c'
    binary = Path(tmp) / 'test'
    source.write_text(prefix + core + release + tests)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-g', '-O1',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(source), '-lzstd', '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
