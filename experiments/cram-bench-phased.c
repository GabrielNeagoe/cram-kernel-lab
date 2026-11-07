#define _GNU_SOURCE
#include <emmintrin.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#define PAGE 4096UL
#define PAGES 8192UL
#define SIZE (PAGE * PAGES)
static const char *run;
static volatile uint64_t sink;
static struct rusage before;
static double started;

static double stamp(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t)) { perror("clock"); exit(1); }
    return t.tv_sec + t.tv_nsec * 1e-9;
}
static void acknowledge(void) {
    int c;
    do { c = getchar(); if (c == EOF) exit(10); } while (c != '\n');
}
static void begin(const char *phase) {
    printf("PHASE_READY,%s.%s\n", run, phase); fflush(stdout);
    acknowledge();
    getrusage(RUSAGE_SELF, &before); started = stamp();
}
static void end(const char *phase) {
    double elapsed = stamp() - started;
    struct rusage after; getrusage(RUSAGE_SELF, &after);
    printf("MEASURE,%s.%s,%.6f,%ld,%ld\n", run, phase, elapsed * 1000,
           after.ru_minflt - before.ru_minflt, after.ru_majflt - before.ru_majflt);
    printf("PHASE_DONE,%s.%s\n", run, phase); fflush(stdout);
    acknowledge();
}
static long demotions(void) {
    long n; FILE *f = fopen("/sys/kernel/debug/cram/demote_count", "r");
    if (!f || fscanf(f, "%ld", &n) != 1) exit(11);
    fclose(f); return n;
}
static void flush(unsigned char *p) {
    for (size_t i = 0; i < PAGES; i++) _mm_clflush(p + i * PAGE);
    _mm_mfence();
}
static void read_pages(unsigned char *p, size_t *order) {
    volatile unsigned char *v = p;
    uint64_t sum = 0;
    for (size_t i = 0; i < PAGES; i++) sum += v[order[i] * PAGE];
    sink += sum;
}
int main(int argc, char **argv) {
    if (argc != 3 || (strcmp(argv[2], "direct") && strcmp(argv[2], "preflush"))) return 12;
    run = argv[1];
    if (sysconf(_SC_PAGESIZE) != PAGE) return 13;
    unsigned char *p = mmap(NULL, SIZE, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED || madvise(p, SIZE, MADV_NOHUGEPAGE)) return 14;
    size_t *order = malloc(PAGES * sizeof(*order));
    if (!order) return 15;
    for (size_t i = 0; i < PAGES; i++) {
        order[i] = i; memset(p + i * PAGE, (i * 37 + 11) & 255, PAGE);
    }
    uint32_t rng = 0x9e3779b9;
    for (size_t i = PAGES - 1; i > 0; i--) {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        size_t j = rng % (i + 1), temp = order[i]; order[i] = order[j]; order[j] = temp;
    }
    begin("idle"); usleep(200000); end("idle");
    flush(p); begin("dram_read"); read_pages(p, order); end("dram_read");
    /* Allow deferred LRU activity to settle; still reject any partial demotion. */
    sleep(1);
    if (madvise(p, SIZE, MADV_COLD)) return 16;
    long previous = demotions();
    begin("demote");
    int rc = madvise(p, SIZE, MADV_PAGEOUT);
    end("demote");
    long delta = demotions() - previous;
    printf("DEMOTED,%s,%ld\n", run, delta); fflush(stdout);
    if (rc || delta != PAGES) return 17;
    if (!strcmp(argv[2], "preflush")) { begin("preflush"); flush(p); end("preflush"); }
    begin("first_read"); read_pages(p, order); end("first_read");
    begin("second_read"); read_pages(p, order); end("second_read");
    begin("cow_write");
    for (size_t i = 0; i < PAGES; i++) p[order[i] * PAGE] ^= 0x5a;
    end("cow_write");
    for (size_t i = 0; i < SIZE; i++) {
        unsigned char expected = ((i / PAGE) * 37 + 11) & 255;
        if (!(i % PAGE)) expected ^= 0x5a;
        if (p[i] != expected) { printf("INTEGRITY,%s,FAIL,%zu\n", run, i); return 18; }
    }
    printf("INTEGRITY,%s,PASS\n", run);
    free(order); munmap(p, SIZE); return 0;
}
