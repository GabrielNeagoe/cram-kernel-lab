#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sched.h>
#include <sys/mman.h>
#include <time.h>

static size_t bytes = 64UL * 1024 * 1024;
#define BYTES bytes
static uint64_t source_pfns[32768];
static int trace_source;
static void source_state(const char *stage)
{
    if (!trace_source) return;
    int fd = open("/proc/kpageflags", O_RDONLY);
    if (fd < 0) exit(21);
    unsigned long buddy = 0, lru = 0, anon = 0;
    for (size_t i = 0; i < BYTES / 4096; i++) {
        uint64_t flags;
        if (pread(fd, &flags, 8, source_pfns[i] * 8) != 8) exit(22);
        buddy += !!(flags & (1ULL << 10));
        lru += !!(flags & (1ULL << 5));
        anon += !!(flags & (1ULL << 12));
    }
    close(fd);
    printf("SOURCE_STATE=%s buddy_heads=%lu lru=%lu anon=%lu\n", stage, buddy, lru, anon);
}
static void checkpoint(const char *stage)
{
    printf("CHECKPOINT=%s\n", stage);
    fflush(stdout);
    char line[32];
    if (!fgets(line, sizeof(line), stdin) || strcmp(line, "GO\n")) exit(20);
    if (strcmp(stage, "idle")) source_state(stage);
}
static uint64_t next(uint64_t *s)
{
    *s ^= *s << 13; *s ^= *s >> 7; *s ^= *s << 17;
    return *s;
}
static uint64_t value(uint64_t *s, size_t i, const char *pattern)
{
    uint64_t r = next(s);
    if (!strcmp(pattern, "repeat") || (!strcmp(pattern, "mixed") && (i / 512) % 2 == 0))
        return UINT64_C(0xabababababababab);
    return r;
}
static long on_cram(void *p)
{
    int fd = open("/proc/self/pagemap", O_RDONLY);
    if (fd < 0) return -1;
    long count = 0;
    for (size_t i = 0; i < BYTES; i += 4096) {
        uint64_t entry;
        off_t offset = ((uintptr_t)p + i) / 4096 * 8;
        if (pread(fd, &entry, 8, offset) != 8 || !(entry & (UINT64_C(1) << 63))) {
            close(fd); return -1;
        }
        uint64_t phys = (entry & ((UINT64_C(1) << 55) - 1)) * 4096;
        if (phys >= UINT64_C(0x490000000) && phys < UINT64_C(0x4a0000000)) count++;
    }
    close(fd); return count;
}
static double now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void balloon(unsigned ratio)
{
    FILE *f = fopen("/sys/bus/dax/devices/dax0.0/cram_compression_ratio", "w");
    if (!f || fprintf(f, "%u\n", ratio) < 0 || fclose(f)) exit(30);
    for (unsigned attempt = 0; attempt < 1200; attempt++) {
        char line[256]; unsigned node, configured, current;
        unsigned long perceived, held, target;
        f = fopen("/sys/kernel/debug/cram/nodes", "r");
        if (!f) exit(31);
        int ready = 0;
        while (fgets(line, sizeof(line), f)) {
            if (sscanf(line, "%u %u %u %lu %lu %lu", &node, &configured,
                       &current, &perceived, &held, &target) == 6 && node == 2 &&
                current == ratio && held == target &&
                (ratio == 3000 ? held == 0 : held > 0)) ready = 1;
        }
        fclose(f);
        if (ready) {
            unsigned long count;
            f = fopen("/sys/bus/dax/devices/dax0.0/cram_trim_count", "r");
            if (!f || fscanf(f, "%lu", &count) != 1) exit(32);
            fclose(f);
            printf("BALLOON_RATIO=%u TRIM_COUNT=%lu\n", ratio, count);
            return;
        }
        usleep(100000);
    }
    exit(33);
}
static void verify_live(uint64_t *p, const char *pattern, const char *stage)
{
    uint64_t seed = 123456789;
    for (size_t i = 0; i < BYTES / 8; i++)
        if (p[i] != value(&seed, i, pattern)) exit(34);
    if (on_cram(p) != (long)(BYTES / 4096)) exit(35);
    printf("LIVE_INTEGRITY=%s PASS\n", stage);
}
int main(int argc, char **argv)
{
    if ((argc != 3 && argc != 4) || (strcmp(argv[1], "dram") && strcmp(argv[1], "cram"))) return 2;
    if (argc == 4) {
        char *end;
        unsigned long mib = strtoul(argv[3], &end, 10);
        if (*end || !mib || mib > 128) return 2;
        bytes = mib * 1024 * 1024;
    }
    if (strcmp(argv[2], "repeat") && strcmp(argv[2], "mixed") && strcmp(argv[2], "random")) return 2;
    if (sysconf(_SC_PAGESIZE) != 4096) return 3;
    cpu_set_t cpus; CPU_ZERO(&cpus); CPU_SET(0, &cpus);
    if (sched_setaffinity(0, sizeof(cpus), &cpus)) return 4;
    checkpoint("idle");
    uint64_t *p = mmap(NULL, BYTES, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED || madvise(p, BYTES, MADV_NOHUGEPAGE)) return 5;
    uint64_t s = 123456789;
    for (size_t i = 0; i < BYTES / 8; i++) p[i] = value(&s, i, argv[2]);
    trace_source = getenv("CRAM_TRACE_SOURCE") != NULL;
    if (trace_source) {
        int fd = open("/proc/self/pagemap", O_RDONLY);
        if (fd < 0) return 23;
        for (size_t i = 0; i < BYTES / 4096; i++) {
            uint64_t entry;
            if (pread(fd, &entry, 8, ((uintptr_t)p / 4096 + i) * 8) != 8 || !(entry & (1ULL << 63))) return 24;
            source_pfns[i] = entry & ((1ULL << 55) - 1);
        }
        close(fd);
        for (size_t i = 0; i < BYTES / 4096;) {
            size_t end = i + 1;
            while (end < BYTES / 4096 && source_pfns[end] == source_pfns[end - 1] + 1) end++;
            printf("SOURCE_RANGE=0x%llx bytes=%zu\n", (unsigned long long)source_pfns[i] * 4096, (end - i) * 4096);
            i = end;
        }
    }
    checkpoint("filled");
    if (madvise(p, BYTES, MADV_COLD)) return 6;
    if (!strcmp(argv[1], "cram")) {
        for (int n = 0; n < 10 && on_cram(p) != (long)(BYTES / 4096); n++) {
            if (madvise(p, BYTES, MADV_PAGEOUT)) return 7;
            usleep(50000);
        }
    }
    long placed = on_cram(p);
    printf("PLACED_PAGES=%ld\n", placed);
    if (placed != (!strcmp(argv[1], "cram") ? (long)(BYTES / 4096) : 0)) return 8;
    checkpoint("placed");
    s = 123456789;
    double started = now();
    for (size_t i = 0; i < BYTES / 8; i++) if (p[i] != value(&s, i, argv[2])) return 9;
    printf("READ_VERIFY_SECONDS=%.6f\nINTEGRITY=PASS\n", now() - started);
    checkpoint("verified");
    if (getenv("CRAM_BALLOON_TEST")) {
        balloon(1500);
        verify_live(p, argv[2], "inflated");
        checkpoint("live_trimmed");
        balloon(3000);
        verify_live(p, argv[2], "restored");
        checkpoint("live_restored");
    }
    if (munmap(p, BYTES)) return 10;
    checkpoint("released");
    if (getenv("CRAM_BALLOON_TEST")) {
        balloon(1000);
        checkpoint("reclaimed");
        balloon(3000);
        checkpoint("restored");
    }
    return 0;
}
