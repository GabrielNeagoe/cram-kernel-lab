/* Standalone sizing prototype, not a QEMU device or CRAM integration. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <zstd.h>

#define PAGE 4096UL
#define BYTES (64UL * 1024 * 1024)
struct block { void *data; size_t length; int raw; };
static uint64_t value(uint64_t *s, size_t i, const char *pattern)
{
    *s ^= *s << 13; *s ^= *s >> 7; *s ^= *s << 17;
    return (!strcmp(pattern, "repeat") || (!strcmp(pattern, "mixed") && (i/512)%2 == 0))
        ? UINT64_C(0xabababababababab) : *s;
}
static void checkpoint(const char *stage)
{
    printf("CHECKPOINT=%s\n", stage); fflush(stdout);
    char line[32];
    if (!fgets(line, sizeof(line), stdin) || strcmp(line, "GO\n")) exit(20);
}
static double now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec+t.tv_nsec/1e9;
}
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (strcmp(argv[1], "repeat") && strcmp(argv[1], "mixed") && strcmp(argv[1], "random")) return 2;
    printf("ZSTD_VERSION=%s\n", ZSTD_versionString());
    checkpoint("idle");
    uint64_t *raw = mmap(NULL, BYTES, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (raw == MAP_FAILED) return 3;
    uint64_t state = 123456789;
    for (size_t i=0; i<BYTES/8; i++) raw[i]=value(&state, i, argv[1]);
    checkpoint("filled");
    struct block *blocks = calloc(BYTES/PAGE, sizeof(*blocks));
    ZSTD_CCtx *ctx = ZSTD_createCCtx();
    size_t capacity = ZSTD_compressBound(PAGE), payload=0, raw_blocks=0;
    void *temporary = malloc(capacity);
    if (!blocks || !ctx || !temporary) return 4;
    double begin=now();
    for (size_t i=0; i<BYTES/PAGE; i++) {
        void *source=(char *)raw+i*PAGE;
        size_t n=ZSTD_compressCCtx(ctx, temporary, capacity, source, PAGE, 1);
        if (ZSTD_isError(n)) return 5;
        blocks[i].raw = n>=PAGE;
        blocks[i].length = blocks[i].raw ? PAGE : n;
        blocks[i].data=malloc(blocks[i].length);
        if (!blocks[i].data) return 6;
        memcpy(blocks[i].data, blocks[i].raw ? source : temporary, blocks[i].length);
        payload+=blocks[i].length; raw_blocks+=blocks[i].raw;
    }
    printf("COMPRESS_SECONDS=%.6f\nPAYLOAD_BYTES=%zu\nINDEX_BYTES=%zu\nRAW_BLOCKS=%zu\n",
           now()-begin, payload, BYTES/PAGE*sizeof(*blocks), raw_blocks);
    checkpoint("both_copies");
    if (munmap(raw, BYTES)) return 7;
    ZSTD_freeCCtx(ctx); free(temporary);
    checkpoint("stored");
    uint64_t decoded[PAGE/8];
    state=123456789; begin=now();
    for (size_t i=0; i<BYTES/PAGE; i++) {
        if (blocks[i].raw) memcpy(decoded, blocks[i].data, PAGE);
        else if (ZSTD_decompress(decoded, PAGE, blocks[i].data, blocks[i].length)!=PAGE) return 8;
        for (size_t j=0; j<PAGE/8; j++)
            if (decoded[j]!=value(&state, i*PAGE/8+j, argv[1])) return 9;
    }
    printf("DECOMPRESS_VERIFY_SECONDS=%.6f\nINTEGRITY=PASS\n", now()-begin);
    checkpoint("verified");
    for (size_t i=0; i<BYTES/PAGE; i++) free(blocks[i].data);
    free(blocks);
    return 0;
}
