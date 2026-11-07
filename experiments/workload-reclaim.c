#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <linux/kernel-page-flags.h>

struct worker { pid_t pid; int command, reply; uintptr_t address; size_t bytes; uint64_t *original; const char *group; };
static void fail(const char *s) { perror(s); exit(20); }
static void put(const char *path,const char *value) {
    int fd=open(path,O_WRONLY);if(fd<0) fail(path);
    if(write(fd,value,strlen(value))!=(ssize_t)strlen(value)) fail(path);
    close(fd);
}
static uint64_t next(uint64_t *s) { *s^=*s<<13;*s^=*s>>7;*s^=*s<<17;return *s; }
static uint64_t value(uint64_t *s,size_t i,const char *pattern) {
    uint64_t r=next(s);
    return !strcmp(pattern,"repeat") || (!strcmp(pattern,"mixed") && (i/512)%2==0)
        ? UINT64_C(0xabcdef1234567800)+(i%32):r;
}
static unsigned long long number(const char *path) {
    unsigned long long v;FILE *f=fopen(path,"r");
    if(!f || fscanf(f,"%llu",&v)!=1) fail(path);
    fclose(f);return v;
}
static void checkpoint(const char *stage) {
    char buf[16];printf("CHECKPOINT=%s\n",stage);fflush(stdout);
    if(!fgets(buf,sizeof(buf),stdin) || strcmp(buf,"GO\n")) exit(21);
}
static struct worker spawn(const char *group,size_t bytes,const char *pattern) {
    int commands[2],replies[2];if(pipe(commands)||pipe(replies)) fail("pipe");
    pid_t pid=fork();if(pid<0) fail("fork");
    if(!pid) {
        close(commands[1]);close(replies[0]);
        char path[160];snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/cgroup.procs",group);put(path,"0");
        uint64_t *p=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        if(p==MAP_FAILED || madvise(p,bytes,MADV_NOHUGEPAGE)) _exit(22);
        uint64_t seed=123456789;
        for(size_t i=0;i<bytes/8;i++) p[i]=value(&seed,i,pattern);
        if(madvise(p,bytes,MADV_COLD)) _exit(23);
        uintptr_t address=(uintptr_t)p;
        if(write(replies[1],&address,sizeof(address))!=sizeof(address)) _exit(24);
        for(;;) {
            char c;if(read(commands[0],&c,1)!=1) _exit(25);
            if(c=='X') { if(munmap(p,bytes)) _exit(26);_exit(0); }
            int bad=0;seed=123456789;
            for(size_t i=0;i<bytes/8;i++) if(p[i]!=value(&seed,i,pattern)) bad=1;
            if(write(replies[1],&bad,sizeof(bad))!=sizeof(bad)) _exit(27);
        }
    }
    close(commands[0]);close(replies[1]);
    struct worker w={.pid=pid,.command=commands[1],.reply=replies[0],.bytes=bytes,.group=group};
    if(read(w.reply,&w.address,sizeof(w.address))!=sizeof(w.address)) fail("worker startup");
    w.original=calloc(bytes/4096,sizeof(uint64_t));if(!w.original) fail("calloc");
    return w;
}
static size_t pages(struct worker *w,const char *stage,int capture) {
    char path[80];snprintf(path,sizeof(path),"/proc/%d/pagemap",w->pid);
    int fd=open(path,O_RDONLY),flags=open("/proc/kpageflags",O_RDONLY);
    if(fd<0 || flags<0) fail("page accounting");
    size_t present=0,swapped=0,anon=0,cache=0,buddy=0;
    for(size_t i=0;i<w->bytes/4096;i++) {
        uint64_t e,f;
        if(pread(fd,&e,8,(w->address/4096+i)*8)!=8) fail("pagemap");
        present+=!!(e&(1ULL<<63));swapped+=!!(e&(1ULL<<62));
        if(capture) {
            if(!(e&(1ULL<<63))) exit(28);
            w->original[i]=e&((1ULL<<55)-1);
            if(!w->original[i]) exit(29);
        }
        if(pread(flags,&f,8,w->original[i]*8)!=8) fail("kpageflags");
        anon+=!!(f&(1ULL<<KPF_ANON));cache+=!!(f&(1ULL<<KPF_SWAPCACHE));buddy+=!!(f&(1ULL<<KPF_BUDDY));
    }
    close(fd);close(flags);
    if(stage) printf("PAGES stage=%s group=%s present=%zu swapped=%zu original_anon=%zu original_swapcache=%zu buddy_heads=%zu\n",stage,w->group,present,swapped,anon,cache,buddy);
    return swapped;
}
static void fields(const char *path,const char *stage,const char *scope) {
    char line[256],key[128];unsigned long long v;FILE *f=fopen(path,"r");if(!f) fail(path);
    while(fgets(line,sizeof(line),f)) if(sscanf(line,"%127s %llu",key,&v)==2)
        printf("FIELD stage=%s scope=%s key=%s value=%llu\n",stage,scope,key,v);
    fclose(f);
}
static void snapshot(const char *stage,struct worker *target,struct worker *neighbor,int live) {
    const char *files[]={"memory.current","memory.swap.current","memory.zswap.current"};
    struct worker *workers[]={target,neighbor};char path[160];
    for(unsigned i=0;i<2;i++) {
        struct worker *w=workers[i];
        for(unsigned j=0;j<3;j++) {
            snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/%s",w->group,files[j]);
            printf("FIELD stage=%s scope=%s key=%s value=%llu\n",stage,w->group,files[j],number(path));
        }
        snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/memory.stat",w->group);fields(path,stage,w->group);
        snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/memory.events",w->group);fields(path,stage,w->group);
        if(live) pages(w,stage,0);
    }
    fields("/proc/meminfo",stage,"guest");fields("/proc/vmstat",stage,"vmstat");
    printf("FIELD stage=%s scope=zswap key=pool_bytes value=%llu\n",stage,number("/sys/kernel/debug/zswap/pool_total_size"));
    printf("FIELD stage=%s scope=zswap key=stored_pages value=%llu\n",stage,number("/sys/kernel/debug/zswap/stored_pages"));
    FILE *f=fopen("/sys/block/vda/stat","r");unsigned long long stats[11];if(!f) fail("disk stat");
    for(int i=0;i<11;i++) if(fscanf(f,"%llu",&stats[i])!=1) exit(30);
    fclose(f);
    printf("FIELD stage=%s scope=disk key=read_sectors value=%llu\n",stage,stats[2]);
    printf("FIELD stage=%s scope=disk key=written_sectors value=%llu\n",stage,stats[6]);
    checkpoint(stage);
}
static void verify(struct worker *w) {
    int bad;if(write(w->command,"V",1)!=1 || read(w->reply,&bad,sizeof(bad))!=sizeof(bad) || bad) exit(31);
    printf("INTEGRITY=%s PASS\n",w->group);
}
int main(int argc,char **argv) {
    if(argc!=2 || (strcmp(argv[1],"mixed") && strcmp(argv[1],"random"))) return 2;
    cpu_set_t cpu;CPU_ZERO(&cpu);CPU_SET(0,&cpu);if(sched_setaffinity(0,sizeof(cpu),&cpu)) return 3;
    struct worker target=spawn("target",32UL*1024*1024,argv[1]);
    struct worker neighbor=spawn("neighbor",16UL*1024*1024,"repeat");
    pages(&target,"initial",1);pages(&neighbor,"initial",1);
    snapshot("filled",&target,&neighbor,1);
    for(int attempt=0;attempt<4 && pages(&target,NULL,0)<target.bytes/4096;attempt++) {
        int fd=open("/sys/fs/cgroup/target/memory.reclaim",O_WRONLY);if(fd<0) fail("reclaim open");
        const char *request="32M swappiness=max";
        errno=0;ssize_t result=write(fd,request,strlen(request));int error=errno;close(fd);
        printf("RECLAIM attempt=%d result=%zd errno=%d\n",attempt,result,error);
        if(result<0 && error!=EAGAIN) exit(32);
        usleep(200000);
    }
    if(pages(&target,NULL,0)!=target.bytes/4096 || pages(&neighbor,NULL,0)!=0) exit(33);
    snapshot("reclaimed",&target,&neighbor,1);
    verify(&neighbor);verify(&target);
    snapshot("restored",&target,&neighbor,1);
    struct worker *workers[]={&target,&neighbor};
    for(unsigned i=0;i<2;i++) {
        int status;struct worker *w=workers[i];
        if(write(w->command,"X",1)!=1 || waitpid(w->pid,&status,0)!=w->pid || !WIFEXITED(status) || WEXITSTATUS(status)) exit(34);
        free(w->original);close(w->command);close(w->reply);
    }
    sleep(2);snapshot("released",&target,&neighbor,0);
    puts("WORKLOAD_RECLAIM_PASS");return 0;
}
