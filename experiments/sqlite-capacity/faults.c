/* Time the first read of real SQLite image pages. Keep fault classification
 * outside the measured interval and use per-thread counters with four readers. */
#define _GNU_SOURCE
#include <stdint.h>
#include <sys/resource.h>
#include <time.h>
#include <stddef.h>

static uint64_t now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000 + ts.tv_nsec;
}

int sample_faults(const volatile unsigned char *base, size_t bytes,
                  unsigned start_page, uint64_t *samples, int *hits)
{
    struct rusage before, after;
    int count = 0;
    *hits = 0;
    (void)now();
    for (size_t page = start_page; page < bytes / 4096; page += 64) {
        if (getrusage(RUSAGE_THREAD, &before)) return -1;
        uint64_t t = now();
        (void)base[page * 4096];
        t = now() - t;
        if (getrusage(RUSAGE_THREAD, &after)) return -1;
        if (after.ru_minflt + after.ru_majflt > before.ru_minflt + before.ru_majflt)
            samples[count++] = t;
        else
            (*hits)++;
    }
    return count;
}
