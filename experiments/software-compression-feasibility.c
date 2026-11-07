#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/wait.h>

#define BYTES (16UL*1024*1024)
static uint64_t next(uint64_t *s) { *s^=*s<<13; *s^=*s>>7; *s^=*s<<17; return *s; }
static uint64_t value(uint64_t *s,size_t i,const char *pattern) {
    uint64_t r=next(s);
    return !strcmp(pattern,"repeat") || (!strcmp(pattern,"mixed") && (i/512)%2==0)
        ? UINT64_C(0xabcdef1234567800)+(i%32) : r;
}
static unsigned long counter(const char *name) {
    char path[160]; unsigned long v;
    snprintf(path,sizeof(path),"/sys/kernel/debug/zswap/%s",name);
    FILE *f=fopen(path,"r");
    if(!f || fscanf(f,"%lu",&v)!=1) exit(10);
    fclose(f); return v;
}
static long swapped(void *p) {
    int fd=open("/proc/self/pagemap",O_RDONLY);long n=0;
    if(fd<0) exit(11);
    for(size_t i=0;i<BYTES;i+=4096) {
        uint64_t e;
        if(pread(fd,&e,8,(((uintptr_t)p+i)/4096)*8)!=8) exit(12);
        n+=!!(e&(UINT64_C(1)<<62));
    }
    close(fd);return n;
}
static void snapshot(const char *stage,void *p) {
    printf("SNAPSHOT=%s swapped=%ld stored=%lu pool_bytes=%lu\n",stage,p?swapped(p):0,
           counter("stored_pages"),counter("pool_total_size"));
    fflush(stdout);
}
static int verify(uint64_t *p,const char *pattern) {
    uint64_t seed=123456789;
    for(size_t i=0;i<BYTES/8;i++) if(p[i]!=value(&seed,i,pattern)) return 1;
    return 0;
}
int main(int argc,char **argv) {
    if(argc!=2 || (strcmp(argv[1],"repeat") && strcmp(argv[1],"mixed") && strcmp(argv[1],"random"))) return 2;
    if(sysconf(_SC_PAGESIZE)!=4096) return 3;
    cpu_set_t cpus;CPU_ZERO(&cpus);CPU_SET(0,&cpus);
    if(sched_setaffinity(0,sizeof(cpus),&cpus)) return 4;
    uint64_t *p=mmap(NULL,BYTES,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(p==MAP_FAILED || madvise(p,BYTES,MADV_NOHUGEPAGE)) return 5;
    snapshot("idle",NULL);
    uint64_t seed=123456789;
    for(size_t i=0;i<BYTES/8;i++) p[i]=value(&seed,i,argv[1]);
    snapshot("filled",p);
    if(madvise(p,BYTES,MADV_COLD)) return 6;
    for(int i=0;i<100 && swapped(p)!=(long)(BYTES/4096);i++) {
        if(madvise(p,BYTES,MADV_PAGEOUT)) return 7;
        usleep(100000);
    }
    snapshot("compressed",p);
    if(swapped(p)!=(long)(BYTES/4096)) return 8;
    /* Fork while mappings are swapped; child must not alter parent's data. */
    pid_t child=fork();if(child<0) return 13;
    if(!child) {
        if(verify(p,argv[1])) _exit(14);
        memset(p,0x39,4096); _exit(0);
    }
    int status;if(waitpid(child,&status,0)!=child || !WIFEXITED(status) || WEXITSTATUS(status)) return 15;
    if(verify(p,argv[1])) return 16;
    snapshot("restored",p);
    if(munmap(p,BYTES)) return 17;
    usleep(250000);
    snapshot("released",NULL);
    printf("PATTERN=%s INTEGRITY=PASS FORK_COW=PASS\n",argv[1]);
    return 0;
}
