/* Diagnostic variant only: runs after a phased benchmark rejects partial demotion. */
#include <fcntl.h>
static void inspect_partial(unsigned char *p, const char *id) {
    uint64_t start, size, entries[PAGES];
    FILE *f=fopen("/sys/bus/cxl/devices/region0/resource","r");
    unsigned long long temp;
    if(!f || fscanf(f,"%llx",&temp)!=1) exit(30);
    start=temp;fclose(f);
    f=fopen("/sys/bus/cxl/devices/region0/size","r");
    if(!f || fscanf(f,"%llx",&temp)!=1) exit(31);
    size=temp;fclose(f);
    int fd=open("/proc/self/pagemap",O_RDONLY), full=0;
    if(fd<0) exit(32);
    for(int pass=0;pass<4;pass++) {
        if(pread(fd,entries,sizeof(entries),((uintptr_t)p/PAGE)*8)!=(ssize_t)sizeof(entries)) exit(33);
        unsigned cxl=0,other=0,absent=0,zero=0;
        for(size_t i=0;i<PAGES;i++) {
            uint64_t address=(entries[i]&((1ULL<<55)-1))*PAGE;
            if(!(entries[i]>>63)) absent++;
            else if(!address) zero++;
            else if(address>=start && address<start+size)cxl++;
            else other++;
        }
        printf("PAGESTATE,%s,pass=%d,cxl=%u,other=%u,absent=%u,zero=%u\n",id,pass,cxl,other,absent,zero);
        if(absent || zero) exit(34);
        if(cxl==PAGES) {full=1;break;}
        if(pass==3)break;
        usleep(50000);
        long before_retry=demotions();
        for(size_t i=0;i<PAGES;i++) {
            uint64_t address=(entries[i]&((1ULL<<55)-1))*PAGE;
            if(address>=start && address<start+size)continue;
            if(madvise(p+i*PAGE,PAGE,MADV_COLD) || madvise(p+i*PAGE,PAGE,MADV_PAGEOUT))exit(35);
        }
        printf("RETRY_PARTIAL,%s,pass=%d,demoted=%ld\n",id,pass+1,demotions()-before_retry);
    }
    close(fd);
    for(size_t i=0;i<SIZE;i++)if(p[i]!=(unsigned char)(((i/PAGE)*37+11)&255))exit(36);
    printf("PARTIAL_INTEGRITY,%s,READ_PASS,all_cxl=%d\n",id,full);fflush(stdout);
}
