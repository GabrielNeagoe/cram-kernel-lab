"""Run a deterministic userspace differential check on the patched kernel slot helpers."""
import argparse
import subprocess
import tempfile
from pathlib import Path

# Small bitmap API shims let the same ca_slot and ca_update_fit bodies run outside the kernel.
HEADER = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define CA_SLOTS 64U
#define CA_MAX_RUN 17U
#define CHUNKS 67U
#define WORDS ((CHUNKS + 63) / 64)
#define round_up(x,a) (((x) + (a) - 1) & ~((a) - 1))
typedef uint64_t u64;
static struct control { unsigned long bitmap[CHUNKS]; } control, *ca = &control;
static unsigned long ca_fit[CA_MAX_RUN * WORDS];
static unsigned ca_row_words = WORDS, ca_blocks = CHUNKS * CA_SLOTS;
static bool cram_index = true, cram_demand = true;
static u64 ca_index_ns;
static u64 ca_tick(void) { return 0; }
static void ca_elapsed(u64 start, u64 *counter) { (void)start; (void)counter; }
static void __set_bit(unsigned i, unsigned long *p) { p[i/64] |= 1UL << (i%64); }
static void __clear_bit(unsigned i, unsigned long *p) { p[i/64] &= ~(1UL << (i%64)); }
static int bit(const unsigned long *p, unsigned i) { return (p[i/64] >> (i%64)) & 1; }
static unsigned find_first_bit(const unsigned long *p, unsigned n) {
    unsigned i = 0;
    while (i < n && !bit(p,i)) i++;
    return i;
}
static unsigned bitmap_find_next_zero_area(const unsigned long *p, unsigned end,
                                            unsigned first, unsigned n, unsigned align) {
    assert(!align);
    unsigned count = 0;
    for (unsigned i=first; i<end; i++) {
        count = bit(p,i) ? 0 : count+1;
        if (count == n) return i+1-n;
    }
    return end;
}
"""

CHECK = r"""
static unsigned long random_state = 0x267b1ab534e3c1UL;
static unsigned long random_word(void) {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 7;
    return random_state ^= random_state << 17;
}
int main(void) {
    _Static_assert(sizeof(unsigned long)==8, "requires 64-bit unsigned long");
    for (unsigned chunk=0; chunk<CHUNKS; chunk++) ca_update_fit(chunk);
    unsigned checks = 0;
    for (unsigned step=0; step<4096; step++) {
        unsigned chunk = random_word()%CHUNKS;
        unsigned run = 1+random_word()%CA_MAX_RUN;
        unsigned start = random_word()%(CA_SLOTS-run+1);
        if (step%128==0) {
            memset(ca->bitmap, (step/128)%2 ? 0xff : 0, sizeof(ca->bitmap));
            for (unsigned c=0; c<CHUNKS; c++) ca_update_fit(c);
        }
        unsigned long mask = ((1UL<<run)-1)<<start;
        if (step%3==0) ca->bitmap[chunk] = random_word();
        else if (step%3==1) ca->bitmap[chunk] |= mask;
        else ca->bitmap[chunk] &= ~mask;
        ca_update_fit(chunk);
        for (unsigned n=1; n<=CA_MAX_RUN; n++) {
            cram_index = false;
            unsigned legacy = ca_slot(n);
            cram_index = true;
            assert(ca_slot(n) == legacy);
            checks++;
        }
    }
    printf("%u matching first-fit decisions\n", checks);
    return 0;
}
"""


def run(source, compiler):
    start = source.index("static void ca_update_fit(")
    end = source.index("static struct ca_entry *ca_at(", start)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "check.c").write_text(HEADER + source[start:end] + CHECK)
        exe = work / "check"
        subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(work / "check.c"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arena_include", type=Path)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    run(args.arena_include.read_text(), args.cc)
