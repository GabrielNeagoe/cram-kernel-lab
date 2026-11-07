/* Reuse allocation helpers and measured-read implementation from the prior probe. */
#define main placement_probe_main
#include "cram-placement-probe.c"
#undef main

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
    cold_read(run,"dram_cold_first","dram_cold_repeat");
    cold_read(run,"dram_cold_again","dram_cold_again_repeat");
    if(madvise(memory,SIZE,MADV_COLD))return 3;
    long before=demotions();
    if(madvise(memory,SIZE,MADV_PAGEOUT))return 4;
    long delta=demotions()-before;
    printf("DEMOTION,%s,first=%ld,cpu=%d\n",run,delta,sched_getcpu());
    if(delta!=PAGES)return 17;
    read_pass(run,"cram_first",0);read_pass(run,"cram_repeat",0);
    cold_read(run,"cram_cold_first","cram_cold_repeat");
    cold_read(run,"cram_cold_again","cram_cold_again_repeat");
    if(placement(run,1)!=PAGES)return 21;
    for(size_t i=0;i<SIZE;i++)if(memory[i]!=(unsigned char)(((i/PAGE)*37+11)&255))return 18;
    for(size_t i=0;i<PAGES;i++)memory[i*PAGE]^=0x5a;
    for(size_t i=0;i<SIZE;i++){
        unsigned char expected=((i/PAGE)*37+11)&255;if(!(i%PAGE))expected^=0x5a;
        if(memory[i]!=expected)return 19;
    }
    printf("INTEGRITY,%s,PASS\n",run);munmap(memory,SIZE);return 0;
}
