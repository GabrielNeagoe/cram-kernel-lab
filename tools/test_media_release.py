"""Sanitizer tests of the exact mailbox handler with transport/lifecycle shims."""
from pathlib import Path
import runpy
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
base = runpy.run_path(str(root / 'tools/test_store_release.py'))
prefix = base['prefix'].replace('typedef int MemTxAttrs;',
                              'typedef struct { int unused; } MemTxAttrs;')
prefix = prefix.replace('#define g_try_malloc malloc', '''
static bool fail_alloc;
static void *test_malloc(size_t n) { return fail_alloc ? NULL : malloc(n); }
#define g_try_malloc test_malloc
''')
prefix = prefix.replace('typedef struct CXLCT3State {', '''
typedef uint64_t MemoryRegion;
typedef struct CXLType3Dev { MemoryRegion *hostvmem; void *hostvmem_as; } CXLType3Dev;
typedef struct CXLCT3State {
    CXLType3Dev ct3d;
''')
shims = r'''
typedef int CXLRetCode;
struct cxl_cmd { int unused; };
typedef struct { void *d; } CXLCCI;
#define CXL_CT3(p) ((CXLCT3State *)(p))
#define CXL_MBOX_SUCCESS 0
#define CXL_MBOX_UNSUPPORTED 1
#define CXL_MBOX_INVALID_PAYLOAD_LENGTH 2
#define CXL_MBOX_INVALID_PA 3
#define CXL_MBOX_INTERNAL_ERROR 4
static uint32_t ldl_le_p(const uint8_t *p) {
    uint32_t v=0; for (unsigned i=0;i<4;i++) v|=(uint32_t)p[i]<<(8*i); return v;
}
static uint64_t ldq_le_p(const uint8_t *p) {
    uint64_t v=0; for (unsigned i=0;i<8;i++) v|=(uint64_t)p[i]<<(8*i); return v;
}
static MemoryRegion *host_memory_backend_get_memory(MemoryRegion *p) { return p; }
static uint64_t memory_region_size(MemoryRegion *p) { return *p; }
static int fail_write, writes;
static uint8_t plain[128*STORE_PAGE];
static MemTxResult address_space_set(void **as, uint64_t addr, uint8_t fill,
                                     uint64_t len, MemTxAttrs attrs) {
    CXLCT3State *s=*as;
    writes++;
    if (fail_write) return MEMTX_ERROR;
    if (!s->store) { memset(plain+addr,fill,len); return MEMTX_OK; }
    for (uint64_t i=0;i<len;i++)
        if (store_write(s,addr+i,fill,1,attrs)!=MEMTX_OK) return MEMTX_ERROR;
    return MEMTX_OK;
}
'''
tests = r'''
static uint8_t payload[40];
static void put64(unsigned offset,uint64_t v) {
    for(unsigned i=0;i<8;i++) payload[offset+i]=v>>(8*i);
}
static int request(CXLCT3State *s,size_t len) {
    CXLCCI cci={.d=s}; size_t out=999;
    int rc=cmd_ct3_media_operations(NULL,payload,len,NULL,&out,&cci);
    assert(out==0); return rc;
}
static uint8_t get(CXLCT3State *s,uint64_t a) {
    uint64_t v; assert(store_read(s,a,&v,1,(MemTxAttrs){0})==MEMTX_OK); return v;
}
static void write_byte(CXLCT3State *s,uint64_t a,uint8_t v) {
    assert(store_write(s,a,v,1,(MemTxAttrs){0})==MEMTX_OK);
}
int main(void) {
    CT3Store store={0}; CXLCT3State s={.store=&store,.vmem_size=128*STORE_PAGE};
    store.pages=128; store.blocks=calloc(128,sizeof(StoreBlock));
    store.context=ZSTD_createCCtx(); assert(store.blocks && store.context);
    s.ct3d.hostvmem=&s.vmem_size; s.ct3d.hostvmem_as=&s;
    for(unsigned p=0;p<128;p++) write_byte(&s,p*STORE_PAGE,0xa5);
    assert(s.store_payload_bytes>0);
    payload[0]=1; payload[1]=1; payload[4]=2;
    put64(8,0); put64(16,STORE_PAGE);
    put64(24,UINT64_MAX-4095); put64(32,8192);
    assert(request(&s,40)==CXL_MBOX_INVALID_PA);
    for(unsigned p=0;p<128;p++) assert(get(&s,p*STORE_PAGE)==0xa5);
    assert(!writes);
    assert(request(&s,39)==CXL_MBOX_INVALID_PAYLOAD_LENGTH);
    assert(request(&s,7)==CXL_MBOX_INVALID_PAYLOAD_LENGTH);
    memset(payload+4,255,4);
    assert(request(&s,40)==CXL_MBOX_INVALID_PAYLOAD_LENGTH);
    memset(payload+4,0,4); payload[4]=1;
    puts("PASS malformed length/count and invalid second range leave data intact");
    put64(8,10*STORE_PAGE); put64(16,100*STORE_PAGE);
    uint64_t before=s.store_payload_bytes;
    assert(request(&s,24)==CXL_MBOX_SUCCESS);
    assert(s.store_payload_bytes<before && !writes);
    for(unsigned p=0;p<128;p++) assert(get(&s,p*STORE_PAGE)==(p>=10&&p<110?0:0xa5));
    assert(request(&s,24)==CXL_MBOX_SUCCESS);
    puts("PASS mailbox aligned zero releases payload and preserves neighbors");
    write_byte(&s,20*STORE_PAGE,0x77);
    put64(8,20*STORE_PAGE+1); put64(16,17);
    assert(request(&s,24)==CXL_MBOX_SUCCESS && writes==0);
    assert(get(&s,20*STORE_PAGE)==0x77);
    payload[1]=0;
    assert(request(&s,24)==CXL_MBOX_SUCCESS);
    for(unsigned i=1;i<=17;i++) assert(get(&s,20*STORE_PAGE+i)==0x0f);
    assert(get(&s,20*STORE_PAGE+18)==0);
    puts("PASS unaligned zero/sanitize semantics without IO reentry");
    for(unsigned p=0;p<128;p++) write_byte(&s,p*STORE_PAGE,0x91);
    put64(8,1); put64(16,17);
    fail_alloc=true;
    assert(request(&s,24)==CXL_MBOX_INTERNAL_ERROR);
    fail_alloc=false;
    for(unsigned p=0;p<128;p++) assert(get(&s,p*STORE_PAGE)==0x91);
    for(unsigned i=1;i<=17;i++) assert(get(&s,i)==0);
    puts("PASS compressed cache-flush allocation failure preserves data");
    assert(store_release(&s,0,s.vmem_size));
    free(store.blocks); ZSTD_freeCCtx(store.context); s.store=NULL;
    memset(plain,0xaa,sizeof(plain)); payload[1]=1;
    put64(8,STORE_PAGE); put64(16,STORE_PAGE);
    assert(request(&s,24)==CXL_MBOX_SUCCESS);
    for(unsigned i=0;i<sizeof(plain);i++) assert(plain[i]==(i>=STORE_PAGE&&i<2*STORE_PAGE?0:0xaa));
    fail_write=1;
    assert(request(&s,24)==CXL_MBOX_INTERNAL_ERROR);
    puts("PASS non-compressed backend zeroing and failure propagation");
    puts("MAILBOX_TESTS_PASS=5");
}
'''
with tempfile.TemporaryDirectory(prefix='cram-media-test-') as tmp:
    source = Path(tmp) / 'test.c'
    binary = Path(tmp) / 'test'
    source.write_text(prefix + base['core'] + base['release'] + shims +
                      (root / 'experiments/ct3-media-release.inc').read_text() + tests)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-g', '-O1',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(source), '-lzstd', '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
