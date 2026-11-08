/* SPDX-License-Identifier: GPL-2.0 */
/* Capacity trial: deterministic anonymous pages, bounded admission, timed first reads. */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
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

static _Atomic unsigned long low_available = ~0UL;
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
    unsigned long previous = atomic_load(&low_available);
    while (v < previous && !atomic_compare_exchange_weak(&low_available, &previous, v)) {}

    if (v < FLOOR_KIB) {
        printf("{\"mib\":%u,\"status\":\"headroom\",\"available_kib\":%lu}\n", mib, v);
        exit(42);
    }
}

static void evict_cold(char *base)
{
    if (madvise(base, MIB, MADV_PAGEOUT)) { perror("MADV_PAGEOUT"); exit(1); }
}

struct sweep_task {
    char *mem;
    unsigned *order, mib, id, workers, round;
    int mixed, pin;
    pthread_barrier_t *start;
    uint64_t *fault, *hit;
    size_t nf, nh;
};

static void *sweep_blocks(void *arg)
{
    struct sweep_task *t = arg;
    uint32_t expected[PAGE/4];
    if (t->pin) {
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(t->id, &set);
        if (sched_setaffinity(0, sizeof(set), &set)) exit(2);
    }
    if (t->workers > 1) {
        int rc = pthread_barrier_wait(t->start);
        if (rc && rc != PTHREAD_BARRIER_SERIAL_THREAD) exit(2);
    }
    for (unsigned j=t->id; j<t->mib; j+=t->workers) {
        unsigned block=t->order[j];
        char *base=t->mem+(size_t)block*MIB;
        int hot=block%4==0;
        for (unsigned q=0; q<256; q++) {
            unsigned page=(q*73+block*11+t->round*19)&255;
            char *address=base+page*PAGE;
            if (!hot && q%32==0) {
                struct rusage a,b;
                if (getrusage(RUSAGE_THREAD,&a)) exit(2);
                uint64_t begin=clock_ns(CLOCK_MONOTONIC_RAW);
                (void)*(volatile const unsigned char *)address;
                uint64_t delta=clock_ns(CLOCK_MONOTONIC_RAW)-begin;
                if (getrusage(RUSAGE_THREAD,&b)) exit(2);
                if (b.ru_minflt+b.ru_majflt > a.ru_minflt+a.ru_majflt) t->fault[t->nf++]=delta;
                else t->hit[t->nh++]=delta;
            }
            page_contents(expected,block*256+page,dense_block(block,t->mixed));
            if (memcmp(address,expected,PAGE)) { fprintf(stderr,"data mismatch\n"); exit(3); }
        }
        if (!hot) evict_cold(base);
        if ((j&7)==0) check_headroom(t->mib);
    }
    return NULL;
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
    static const char *const lock_stats[] = {
        "arena_lookups", "arena_visits", "arena_acquires", "arena_contended", "arena_wait_ns", "arena_hold_ns"
    };
    unsigned long lock_before[6], shard_before[2];
    static const char *const shard_stores[] = {"arena_shard0_stores", "arena_shard1_stores"};
    int sharded = getenv("CRAM_TWO_SHARDS") != NULL;
    int hash_trial = getenv("CRAM_HASH_TRIAL") != NULL;
    unsigned workers = getenv("CRAM_WORKERS") && !strcmp(getenv("CRAM_WORKERS"), "2") ? 2 : 1;
    unsigned long before[4], used = 0, entries = 0;
    if (profiling) {
        if (hash_trial)
            for (unsigned i = 0; i < 6; i++) lock_before[i] = arena_stat(lock_stats[i]);
        if (sharded)
            for (unsigned i = 0; i < 2; i++) shard_before[i] = arena_stat(shard_stores[i]);
        used = arena_stat("arena_used_bytes");
        entries = arena_stat("arena_entries");
        for (unsigned i = 0; i < 4; i++) before[i] = arena_stat(timers[i]);
    }
    unsigned long stores = stat_value("/sys/fs/cgroup/bench/memory.stat","zswpout");
    unsigned long loads = stat_value("/sys/fs/cgroup/bench/memory.stat","zswpin");
    pthread_barrier_t gate;
    pthread_attr_t attr;
    struct sweep_task tasks[2];
    if (pthread_barrier_init(&gate, NULL, workers) || pthread_attr_init(&attr) ||
        pthread_attr_setstacksize(&attr, 256*1024)) return 2;
    for (unsigned w = 0; w < workers; w++) {
        tasks[w] = (struct sweep_task){.mem=mem, .order=order, .mib=mib, .id=w, .workers=workers,
            .mixed=mixed, .pin=hash_trial, .start=&gate, .fault=w ? calloc(slots,sizeof(*fault)) : fault,
            .hit=w ? calloc(slots,sizeof(*hit)) : hit};
        if (!tasks[w].fault || !tasks[w].hit) return 2;
        if (w) {
            memset(tasks[w].fault, 0x55, slots*sizeof(*fault));
            memset(tasks[w].hit, 0x55, slots*sizeof(*hit));
        }
    }
    uint32_t seed=62791;
    uint64_t busy=guest_busy_ticks();
    uint64_t cpu=clock_ns(CLOCK_PROCESS_CPUTIME_ID), wall=clock_ns(CLOCK_MONOTONIC_RAW);
    for (unsigned round=0; round<ROUNDS; round++) {
        for (unsigned j=mib; j>1; j--) {
            unsigned k=random_word(&seed)%j, tmp=order[j-1];
            order[j-1]=order[k]; order[k]=tmp;
        }
        pthread_t peer;
        for (unsigned w = 0; w < workers; w++) tasks[w].round = round;
        if (workers == 2 && pthread_create(&peer, &attr, sweep_blocks, &tasks[1])) return 2;
        sweep_blocks(&tasks[0]);
        if (workers == 2 && pthread_join(peer, NULL)) return 2;
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
    if (hash_trial) {
        printf(",\"workers\":%u", workers);
        if (profiling && sharded)
            for (unsigned i=0; i<2; i++)
                printf(",\"%s\":%lu", shard_stores[i], arena_stat(shard_stores[i]) - shard_before[i]);
        if (profiling)
            for (unsigned i=0; i<6; i++)
                printf(",\"%s\":%lu", lock_stats[i], arena_stat(lock_stats[i]) - lock_before[i]);
    }
    size_t nf=tasks[0].nf, nh=tasks[0].nh;
    if (workers == 2) {
        memcpy(fault + nf, tasks[1].fault, tasks[1].nf * sizeof(*fault));
        memcpy(hit + nh, tasks[1].hit, tasks[1].nh * sizeof(*hit));
        nf += tasks[1].nf;
        nh += tasks[1].nh;
    }
    print_latencies("fault",fault,nf); print_latencies("hit",hit,nh);
    puts("}");
    return 0;
}
