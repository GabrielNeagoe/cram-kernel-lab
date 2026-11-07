#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <sched.h>
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
static volatile uint64_t sink;
static unsigned char *memory;
static size_t order[PAGES];
static uint64_t entries[PAGES], cxl_start, cxl_end;
static long demotions(void) {
    long n; FILE *f=fopen("/sys/kernel/debug/cram/demote_count","r");
    if (!f || fscanf(f,"%ld",&n)!=1) exit(10);
    fclose(f); return n;
}
static uint64_t resource(const char *path) {
    uint64_t n; FILE *f=fopen(path,"r");
    if (!f || fscanf(f,"%" SCNx64,&n)!=1) exit(11);
    fclose(f); return n;
}
static void pin(int cpu) {
    if (cpu < 0) return;
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(cpu,&set);
    if (sched_setaffinity(0,sizeof(set),&set)) {perror("affinity");exit(12);}
}
static int in_cxl(uint64_t entry) {
    uint64_t address=(entry & ((1ULL<<55)-1))*PAGE;
    return (entry>>63) && address>=cxl_start && address<cxl_end;
}
static int placement(const char *run,int pass) {
    int fd=open("/proc/self/pagemap",O_RDONLY);
    if (fd<0 || pread(fd,entries,sizeof(entries),((uintptr_t)memory/PAGE)*8)!=(ssize_t)sizeof(entries)) {perror("pagemap");exit(13);}
    close(fd);
    int cxl=0, other=0, absent=0, zero=0;
    for (size_t i=0;i<PAGES;i++) {
        if (!(entries[i]>>63)) absent++;
        else if (!(entries[i]&((1ULL<<55)-1))) zero++;
        else if (in_cxl(entries[i])) cxl++;
        else other++;
    }
    printf("PLACEMENT,%s,%d,cxl=%d,other=%d,absent=%d,zero_pfn=%d\n",run,pass,cxl,other,absent,zero);
    if (absent || zero) exit(14);
    return cxl;
}
static void read_pass(const char *run,const char *label,int cpu) {
    pin(cpu);
    struct rusage a,b; struct timespec t,u; uint64_t sum=0;
    getrusage(RUSAGE_SELF,&a); clock_gettime(CLOCK_MONOTONIC_RAW,&t);
    for(size_t i=0;i<PAGES;i++) sum+=((volatile unsigned char *)memory)[order[i]*PAGE];
    clock_gettime(CLOCK_MONOTONIC_RAW,&u); getrusage(RUSAGE_SELF,&b); sink+=sum;
    double ms=(u.tv_sec-t.tv_sec)*1000.0+(u.tv_nsec-t.tv_nsec)/1e6;
    printf("READ,%s,%s,cpu=%d,ms=%.6f,minor=%ld,major=%ld\n",run,label,sched_getcpu(),ms,b.ru_minflt-a.ru_minflt,b.ru_majflt-a.ru_majflt);
}
int main(int argc,char **argv) {
    if(argc!=4 || sysconf(_SC_PAGESIZE)!=PAGE) return 1;
    const char *run=argv[1]; int diagnostic=!strcmp(argv[2],"placement"), first=atoi(argv[3]);
    if(!diagnostic && strcmp(argv[2],"cpu")) return 2;
    pin(diagnostic?first:0);
    cxl_start=resource("/sys/bus/cxl/devices/region0/resource");
    cxl_end=cxl_start+resource("/sys/bus/cxl/devices/region0/size");
    memory=mmap(NULL,SIZE,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(memory==MAP_FAILED || madvise(memory,SIZE,MADV_NOHUGEPAGE)) return 3;
    for(size_t i=0;i<PAGES;i++) {order[i]=i;memset(memory+i*PAGE,(i*37+11)&255,PAGE);}
    uint32_t rng=0x9e3779b9;
    for(size_t i=PAGES-1;i;i--) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;size_t j=rng%(i+1),tmp=order[i];order[i]=order[j];order[j]=tmp;}
    if(diagnostic) placement(run,0);
    else {read_pass(run,"dram_first",first);read_pass(run,"dram_other",1-first);pin(0);}
    sleep(1);
    if(madvise(memory,SIZE,MADV_COLD)) return 4;
    long initial=demotions();
    if(madvise(memory,SIZE,MADV_PAGEOUT)) return 5;
    long moved=demotions()-initial;
    printf("DEMOTION,%s,first=%ld,cpu=%d\n",run,moved,sched_getcpu());
    if(diagnostic) {
        int count=placement(run,1);
        for(int pass=2;count<(int)PAGES && pass<=4;pass++) {
            usleep(50000);
            for(size_t i=0;i<PAGES;i++) if(!in_cxl(entries[i])) {
                if(madvise(memory+i*PAGE,PAGE,MADV_COLD) || madvise(memory+i*PAGE,PAGE,MADV_PAGEOUT)) return 6;
            }
            count=placement(run,pass);
            printf("RETRY,%s,%d,cumulative=%ld\n",run,pass,demotions()-initial);
        }
        if(count!=PAGES) {printf("INCOMPLETE,%s\n",run);return 17;}
    } else {
        if(moved!=PAGES) {printf("SKIPPED,%s,partial_demotion\n",run);return 17;}
        /* No pagemap inspection before timing: keep this probe separate. */
        read_pass(run,"cram_first",first);
        read_pass(run,"cram_same",first);
        read_pass(run,"cram_other",1-first);
        read_pass(run,"cram_return",first);
    }
    for(size_t i=0;i<SIZE;i++) if(memory[i]!=(unsigned char)(((i/PAGE)*37+11)&255)) return 18;
    for(size_t i=0;i<PAGES;i++) memory[i*PAGE]^=0x5a;
    for(size_t i=0;i<SIZE;i++) {
        unsigned char expected=((i/PAGE)*37+11)&255;
        if(!(i%PAGE)) expected^=0x5a;
        if(memory[i]!=expected) return 19;
    }
    printf("INTEGRITY,%s,PASS\n",run);munmap(memory,SIZE);return 0;
}
