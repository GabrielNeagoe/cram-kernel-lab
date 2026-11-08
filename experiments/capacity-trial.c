/* SPDX-License-Identifier: GPL-2.0 */
/* Capacity trial: deterministic anonymous pages, bounded admission, timed first reads. */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#define PAGE 4096u
#define MIB (1024u * 1024u)
#define FLOOR_KIB (64u * 1024u)
#define ROUNDS 3u
#define LIMIT_MIB 2048u

static uint32_t random_word(uint32_t *state)
{
    uint32_t v = *state;
    v ^= v << 13;
    v ^= v >> 17;
    v ^= v << 5;
    return *state = v;
}

static void page_contents(uint32_t *page, unsigned index, int dense)
{
    uint32_t state = (index + 1) * UINT32_C(2654435761);
    for (unsigned i = 0; i < PAGE / sizeof(*page); i++)
        page[i] = dense ? random_word(&state) :
            ((i % 8 < 3) ? random_word(&state) & 511 : 0);
}

static uint64_t clock_ns(clockid_t clock_id)
{
    struct timespec t;
    if (clock_gettime(clock_id, &t)) { perror("clock_gettime"); exit(1); }
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}

static unsigned long stat_value(const char *file, const char *field)
{
    FILE *f = fopen(file, "r");
    char line[256], key[80];
    unsigned long v;
    if (!f) { perror(file); exit(1); }
    while (fgets(line, sizeof(line), f))
        if (sscanf(line, "%79s %lu", key, &v) == 2 && !strcmp(field, key)) {
            fclose(f);
            return v;
        }
    fprintf(stderr, "missing %s in %s\n", field, file);
    exit(1);
}

static unsigned long arena_stat(const char *name)
{
    unsigned long bytes;
    char path[128];
    snprintf(path, sizeof(path), "/sys/kernel/debug/zswap/%s", name);
    FILE *f = fopen(path, "r");
    if (!f || fscanf(f, "%lu", &bytes) != 1) exit(2);
    fclose(f);
    return bytes;
}

static int compare(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

static void print_latencies(const char *name, uint64_t *v, size_t n)
{
    qsort(v, n, sizeof(*v), compare);
    printf(",\"%s_samples\":%zu", name, n);
    if (n)
        printf(",\"%s_p50_ns\":%" PRIu64 ",\"%s_p99_ns\":%" PRIu64,
               name, v[(n-1)/2], name, v[(99*n+99)/100-1]);
}

static unsigned long low_available = ~0UL;
static uint64_t guest_busy_ticks(void)
{
    unsigned long long user, nice, sys, idle, wait, irq, soft;
    FILE *f=fopen("/proc/stat","r");
    if (!f || fscanf(f,"cpu %llu %llu %llu %llu %llu %llu %llu",
                     &user,&nice,&sys,&idle,&wait,&irq,&soft)!=7) exit(2);
    fclose(f);
    return user+nice+sys+irq+soft;
}

static int dense_block(unsigned block, int mixed)
{
    unsigned k=block%8;
    return block%4==0 || (mixed && (k==1 || k==3 || k==6));
}

static void check_headroom(unsigned mib)
{
    unsigned long v = stat_value("/proc/meminfo", "MemAvailable:");
    if (v < low_available) low_available = v;
    if (v < FLOOR_KIB) {
        printf("{\"mib\":%u,\"status\":\"headroom\",\"available_kib\":%lu}\n", mib, v);
        exit(42);
    }
}

static void evict_cold(char *base)
{
    if (madvise(base, MIB, MADV_PAGEOUT)) { perror("MADV_PAGEOUT"); exit(1); }
}

int main(int argc, char **argv)
{
    int profiling = argc == 4 && !strcmp(argv[3], "--profile");
    int sample_arena = profiling || (argc == 4 && !strcmp(argv[3], "--arena-stats"));
    unsigned mib = (argc == 3 || sample_arena) ? (unsigned)strtoul(argv[1], NULL, 10) : 0;
    if (!mib || mib > LIMIT_MIB || mib % 4 || sysconf(_SC_PAGESIZE) != PAGE) return 2;
    int mixed=!strcmp(argv[2],"mixed");
    if (!mixed && strcmp(argv[2],"structured")) return 2;
    if (stat_value("/proc/meminfo", "MemTotal:") > 1024*1024) return 2;
    struct rlimit unlimited = {RLIM_INFINITY, RLIM_INFINITY};
    if (setrlimit(RLIMIT_MEMLOCK, &unlimited)) { perror("memlock"); return 2; }
    size_t bytes = (size_t)mib * MIB;
    char *mem = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED || madvise(mem, bytes, MADV_NOHUGEPAGE)) return 2;
    size_t slots = (size_t)mib * ROUNDS * 8;
    uint64_t *fault = calloc(slots, sizeof(*fault)), *hit = calloc(slots, sizeof(*hit));
    unsigned *order = calloc(mib, sizeof(*order));
    if (!fault || !hit || !order) return 2;
    /* Prefault timing buffers so their allocation faults are outside the samples. */
    memset(fault, 0x55, slots*sizeof(*fault));
    memset(hit, 0x55, slots*sizeof(*hit));
    unsigned long disk = stat_value("/proc/vmstat","pswpout");
    unsigned long disk_in = stat_value("/proc/vmstat","pswpin");
    for (unsigned block = 0; block < mib; block++) {
        char *base = mem + (size_t)block*MIB;
        int hot = block % 4 == 0;
        check_headroom(mib);
        for (unsigned q=0; q<256; q++)
            page_contents((uint32_t *)(base+q*PAGE), block*256+q, dense_block(block,mixed));
        if (hot) {
            if (mlock(base,MIB)) { perror("mlock"); return 2; }
        } else evict_cold(base);
        order[block]=block;
    }
    check_headroom(mib);
    unsigned long after_fill = sample_arena ? arena_stat("arena_resident_bytes") : 0;
    static const char *const timers[] = {
        "arena_search_ns", "arena_codec_ns", "arena_hash_ns", "arena_index_ns"
    };
    unsigned long before[4], used = 0, entries = 0;
    if (profiling) {
        used = arena_stat("arena_used_bytes");
        entries = arena_stat("arena_entries");
        for (unsigned i = 0; i < 4; i++) before[i] = arena_stat(timers[i]);
    }
    unsigned long stores = stat_value("/sys/fs/cgroup/bench/memory.stat","zswpout");
    unsigned long loads = stat_value("/sys/fs/cgroup/bench/memory.stat","zswpin");
    size_t nf=0, nh=0;
    uint32_t expected[PAGE/4], seed=62791;
    uint64_t busy=guest_busy_ticks();
    uint64_t cpu=clock_ns(CLOCK_PROCESS_CPUTIME_ID), wall=clock_ns(CLOCK_MONOTONIC_RAW);
    for (unsigned round=0; round<ROUNDS; round++) {
        for (unsigned j=mib; j>1; j--) {
            unsigned k=random_word(&seed)%j, tmp=order[j-1];
            order[j-1]=order[k]; order[k]=tmp;
        }
        for (unsigned j=0; j<mib; j++) {
            unsigned block=order[j];
            char *base=mem+(size_t)block*MIB;
            int hot=block%4==0;
            for (unsigned q=0; q<256; q++) {
                unsigned page=(q*73+block*11+round*19)&255;
                char *address=base+page*PAGE;
                if (!hot && q%32==0) {
                    struct rusage a,b;
                    if (getrusage(RUSAGE_THREAD,&a)) return 2;
                    uint64_t t=clock_ns(CLOCK_MONOTONIC_RAW);
                    (void)*(volatile const unsigned char *)address;
                    uint64_t delta=clock_ns(CLOCK_MONOTONIC_RAW)-t;
                    if (getrusage(RUSAGE_THREAD,&b)) return 2;
                    if (b.ru_minflt+b.ru_majflt > a.ru_minflt+a.ru_majflt) fault[nf++]=delta;
                    else hit[nh++]=delta;
                }
                page_contents(expected,block*256+page,dense_block(block,mixed));
                if (memcmp(address,expected,PAGE)) { fprintf(stderr,"data mismatch\n"); return 3; }
            }
            if (!hot) evict_cold(base);
            if ((j&7)==0) check_headroom(mib);
        }
        check_headroom(mib);
    }
    cpu=clock_ns(CLOCK_PROCESS_CPUTIME_ID)-cpu;
    wall=clock_ns(CLOCK_MONOTONIC_RAW)-wall;
    busy=guest_busy_ticks()-busy;
    if (stat_value("/proc/vmstat","pswpout")!=disk ||
        stat_value("/proc/vmstat","pswpin")!=disk_in) {
        fprintf(stderr,"swap disk traffic\n"); return 4;
    }
    printf("{\"mib\":%u,\"status\":\"pass\",\"rounds\":%u,\"minimum_available_kib\":%lu,"
           "\"verified_mib\":%u,\"worker_cpu_ns\":%" PRIu64 ",\"wall_ns\":%" PRIu64
           ",\"sweep_stores\":%lu,\"sweep_loads\":%lu,\"disk_swap_in_out\":0",
           mib,ROUNDS,low_available,mib*ROUNDS,cpu,wall,
           stat_value("/sys/fs/cgroup/bench/memory.stat","zswpout")-stores,
           stat_value("/sys/fs/cgroup/bench/memory.stat","zswpin")-loads);
    printf(",\"guest_busy_ns\":%" PRIu64, busy*UINT64_C(1000000000)/(uint64_t)sysconf(_SC_CLK_TCK));
    if (sample_arena)
        printf(",\"arena_after_fill_bytes\":%lu", after_fill);
    if (profiling) {
        printf(",\"used_after_fill_bytes\":%lu,\"entries_after_fill\":%lu", used, entries);
        for (unsigned i = 0; i < 4; i++)
            printf(",\"%s\":%lu", timers[i], arena_stat(timers[i]) - before[i]);
    }
    print_latencies("fault",fault,nf); print_latencies("hit",hit,nh);
    puts("}");
    return 0;
}
