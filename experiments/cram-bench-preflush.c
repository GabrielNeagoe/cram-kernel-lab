#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <emmintrin.h>
#include <unistd.h>

#define PAGE 4096UL
#define PAGES 8192UL
#define SIZE (PAGE * PAGES)
#define REPEAT 64
static volatile uint64_t sink;

static double stamp(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts)) {
        perror("clock"); exit(1);
    }
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static double thread_cpu_stamp(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts)) {
        perror("thread cpu clock");
        exit(1);
    }
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static void flush_sample_lines(unsigned char *p) {
    for (size_t i = 0; i < PAGES; ++i)
        _mm_clflush(p + i * PAGE);
    _mm_mfence();
}

static long long demotions(void) {
    FILE *f = fopen("/sys/kernel/debug/cram/demote_count", "r");
    long long n;
    if (!f || fscanf(f, "%lld", &n) != 1) {
        perror("demote_count"); exit(1);
    }
    fclose(f); return n;
}

static void result(const char *name, double seconds, size_t count) {
    printf("RESULT,%s,%.3f,%.1f\n", name, 1000 * seconds,
           1e9 * seconds / count);
    fflush(stdout);
}


static void fault_delta(const char *name,
                        const struct rusage *a,
                        const struct rusage *b) {
    printf("FAULTS,%s,minor=%ld,major=%ld,invol_cs=%ld\n",
           name,
           b->ru_minflt - a->ru_minflt,
           b->ru_majflt - a->ru_majflt,
           b->ru_nivcsw - a->ru_nivcsw);
}

static void read_pages(const char *name, volatile unsigned char *p,
                       const size_t *order, unsigned passes) {
    uint64_t sum = 0;
    struct rusage a, b;
    getrusage(RUSAGE_SELF, &a);
    double t = stamp();
    for (unsigned r = 0; r < passes; ++r)
        for (size_t i = 0; i < PAGES; ++i)
            sum += p[order[i] * PAGE];
    double dt = stamp() - t;
    getrusage(RUSAGE_SELF, &b);
    result(name, dt, PAGES * (size_t)passes);
    fault_delta(name, &a, &b);
    sink += sum;
}

static void write_pages(const char *name, volatile unsigned char *p,
                        const size_t *order) {
    struct rusage a, b;
    getrusage(RUSAGE_SELF, &a);
    double t = stamp();
    for (size_t i = 0; i < PAGES; ++i)
        p[order[i] * PAGE] ^= 0x5a;
    double dt = stamp() - t;
    getrusage(RUSAGE_SELF, &b);
    result(name, dt, PAGES);
    fault_delta(name, &a, &b);
}

int main(void) {
    if (sysconf(_SC_PAGESIZE) != PAGE) return 1;

    unsigned char *dram = mmap(NULL, SIZE, PROT_READ|PROT_WRITE,
                               MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    unsigned char *cram = mmap(NULL, SIZE, PROT_READ|PROT_WRITE,
                               MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (dram == MAP_FAILED || cram == MAP_FAILED) {
        perror("mmap"); return 1;
    }
    if (madvise(dram, SIZE, MADV_NOHUGEPAGE) ||
        madvise(cram, SIZE, MADV_NOHUGEPAGE)) {
        perror("nohugepage"); return 1;
    }

    size_t *order = malloc(PAGES * sizeof(*order));
    if (!order) return 1;

    for (size_t i = 0; i < PAGES; ++i) {
        order[i] = i;
        unsigned char x = (unsigned char)((i * 37 + 11) & 255);
        memset(dram + i * PAGE, x, PAGE);
        memset(cram + i * PAGE, x, PAGE);
    }

    uint32_t rng = 0x9e3779b9;
    for (size_t i = PAGES - 1; i > 0; --i) {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        size_t j = rng % (i + 1);
        size_t tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    }

    puts("FORMAT,operation,elapsed_ms,ns_per_page_visit");
    read_pages("dram_first", dram, order, 1);
    read_pages("dram_repeat", dram, order, REPEAT);
    write_pages("dram_write", dram, order);

    flush_sample_lines(dram);
    read_pages("dram_cache_cold", dram, order, 1);

    if (mprotect(dram, SIZE, PROT_NONE) ||
        mprotect(dram, SIZE, PROT_READ | PROT_WRITE)) {
        perror("mprotect");
        return 1;
    }

    flush_sample_lines(dram);
    read_pages("dram_cache_tlb_cold", dram, order, 1);

    if (madvise(cram, SIZE, MADV_COLD)) {
        perror("cold"); return 1;
    }

    long long before = demotions();
    double cpu_before = thread_cpu_stamp();
    struct rusage dem_a, dem_b;
    getrusage(RUSAGE_SELF, &dem_a);
    double t = stamp();
    if (madvise(cram, SIZE, MADV_PAGEOUT)) {
        perror("pageout"); return 1;
    }
    double dt = stamp() - t;
    double cpu_dt = thread_cpu_stamp() - cpu_before;
    getrusage(RUSAGE_SELF, &dem_b);
    long long delta = demotions() - before;
    result("demotion", dt, PAGES);
    printf("DEMOTION_CPU_MS,%.3f\n", 1000.0 * cpu_dt);
    fault_delta("demotion", &dem_a, &dem_b);
    printf("DEMOTED_PAGES,%lld\nDEMOTION_MiBps,%.2f\n",
           delta, 32.0 / dt);
    if (delta != PAGES) return 2;

    /* Resolve/flush CRAM addresses before their first read */
    struct rusage pf_before, pf_after;
    getrusage(RUSAGE_SELF, &pf_before);
    double preflush_start = stamp();

    flush_sample_lines(cram);

    double preflush_elapsed = stamp() - preflush_start;
    getrusage(RUSAGE_SELF, &pf_after);

    result("cram_preflush", preflush_elapsed, PAGES);
    fault_delta("cram_preflush", &pf_before, &pf_after);

    read_pages("cram_first", cram, order, 1);
    read_pages("cram_second", cram, order, 1);

    /* Evict sampled CRAM cache lines */
    flush_sample_lines(cram);

    /* Read again with translations already established */
    read_pages("cram_cache_cold", cram, order, 1);
    read_pages("cram_after_flush", cram, order, 1);
    read_pages("cram_repeat", cram, order, REPEAT);
    write_pages("cram_cow", cram, order);

    if (memcmp(dram, cram, SIZE) != 0) {
        fprintf(stderr, "INTEGRITY,FAIL\n"); return 3;
    }

    puts("INTEGRITY,PASS");
    printf("SINK,%llu\n", (unsigned long long)sink);
    free(order); munmap(dram, SIZE); munmap(cram, SIZE);
    return 0;
}
