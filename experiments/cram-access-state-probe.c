/* Reuse allocation helpers and measured-read implementation from the prior probe. */
#define main placement_probe_main
#include "cram-placement-probe.c"
#undef main
#include <emmintrin.h>

static void protect_read(const char *run,const char *first,const char *repeat) {
    if(mprotect(memory,SIZE,PROT_NONE) || mprotect(memory,SIZE,PROT_READ|PROT_WRITE))exit(22);
    read_pass(run,first,0);read_pass(run,repeat,0);
}
static void cold_flush(const char *run,const char *flush_label,const char *read_label) {
    if(madvise(memory,SIZE,MADV_COLD))exit(23);
    struct rusage a,b;struct timespec t,u;
    getrusage(RUSAGE_SELF,&a);clock_gettime(CLOCK_MONOTONIC_RAW,&t);
    for(size_t i=0;i<PAGES;i++)_mm_clflush(memory+i*PAGE);
    _mm_mfence();
    clock_gettime(CLOCK_MONOTONIC_RAW,&u);getrusage(RUSAGE_SELF,&b);
    double ms=(u.tv_sec-t.tv_sec)*1000.0+(u.tv_nsec-t.tv_nsec)/1e6;
    printf("FLUSH,%s,%s,cpu=%d,ms=%.6f,minor=%ld,major=%ld\n",run,flush_label,sched_getcpu(),ms,b.ru_minflt-a.ru_minflt,b.ru_majflt-a.ru_majflt);
    read_pass(run,read_label,0);
}

static void cold_read(const char *run,const char *first,const char *repeat) {
    if(madvise(memory,SIZE,MADV_COLD)) {perror("MADV_COLD");exit(20);}
    read_pass(run,first,0);read_pass(run,repeat,0);
}
int main(int argc,char **argv) {
    if(argc!=2 || sysconf(_SC_PAGESIZE)!=PAGE)return 1;
    const char *run=argv[1];pin(0);
    cxl_start=resource("/sys/bus/cxl/devices/region0/resource");
    cxl_end=cxl_start+resource("/sys/bus/cxl/devices/region0/size");
    memory=mmap(NULL,SIZE,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(memory==MAP_FAILED || madvise(memory,SIZE,MADV_NOHUGEPAGE))return 2;
    for(size_t i=0;i<PAGES;i++){order[i]=i;memset(memory+i*PAGE,(i*37+11)&255,PAGE);}
    uint32_t rng=0x9e3779b9;
    for(size_t i=PAGES-1;i;i--){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;size_t j=rng%(i+1),t=order[i];order[i]=order[j];order[j]=t;}
    read_pass(run,"dram_warm",0);
    protect_read(run,"dram_protect_first","dram_protect_repeat");
    cold_read(run,"dram_cold_first","dram_cold_repeat");
    cold_read(run,"dram_cold_again","dram_cold_again_repeat");
    cold_flush(run,"dram_cold_preflush","dram_after_cold_preflush");
    if(madvise(memory,SIZE,MADV_COLD))return 3;
    long before=demotions();
    if(madvise(memory,SIZE,MADV_PAGEOUT))return 4;
    long delta=demotions()-before;
    printf("DEMOTION,%s,first=%ld,cpu=%d\n",run,delta,sched_getcpu());
    if(delta!=PAGES)return 17;
    read_pass(run,"cram_first",0);read_pass(run,"cram_repeat",0);
    protect_read(run,"cram_protect_first","cram_protect_repeat");
    cold_read(run,"cram_cold_first","cram_cold_repeat");
    cold_read(run,"cram_cold_again","cram_cold_again_repeat");
    cold_flush(run,"cram_cold_preflush","cram_after_cold_preflush");
    if(placement(run,1)!=PAGES)return 21;
    for(size_t i=0;i<SIZE;i++)if(memory[i]!=(unsigned char)(((i/PAGE)*37+11)&255))return 18;
    for(size_t i=0;i<PAGES;i++)memory[i*PAGE]^=0x5a;
    for(size_t i=0;i<SIZE;i++){
        unsigned char expected=((i/PAGE)*37+11)&255;if(!(i%PAGE))expected^=0x5a;
        if(memory[i]!=expected)return 19;
    }
    printf("INTEGRITY,%s,PASS\n",run);munmap(memory,SIZE);return 0;
}
