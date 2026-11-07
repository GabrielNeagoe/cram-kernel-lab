#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sched.h>
#include <signal.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/wait.h>

#define MIB (1024UL*1024)
#define WORDS (16*MIB/8)
struct shared { atomic_int stop; atomic_ulong progress[2]; };
struct worker { pid_t pid; int cmd,reply; uintptr_t address; };
static struct shared *shared;
static void die(const char *s) {perror(s);exit(20);}
static void put(const char *path,const char *s) {int f=open(path,O_WRONLY);if(f<0 || write(f,s,strlen(s))!=(ssize_t)strlen(s))die(path);close(f);}
static unsigned long long num(const char *p) {unsigned long long n;FILE*f=fopen(p,"r");if(!f || fscanf(f,"%llu",&n)!=1)die(p);fclose(f);return n;}
static double now(void) {struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))die("clock");return t.tv_sec+t.tv_nsec/1e9;}
static uint64_t word(size_t i,unsigned gen) {
    uint64_t x=i+((uint64_t)gen<<32)+0x9e3779b97f4a7c15ULL;
    if(gen%3==0 || (gen%3==1 && (i/512)%2==0))return 0xabcde00000000000ULL+gen+(i%32);
    x=(x^(x>>30))*0xbf58476d1ce4e5b9ULL;x=(x^(x>>27))*0x94d049bb133111ebULL;return x^(x>>31);
}
static uint64_t *allocbuf(unsigned gen) {
    uint64_t*p=mmap(NULL,16*MIB,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(p==MAP_FAILED || madvise(p,16*MIB,MADV_NOHUGEPAGE))die("mmap");
    for(size_t i=0;i<WORDS;i++)p[i]=word(i,gen);
    return p;
}
static void check(uint64_t*p,unsigned gen) {for(size_t i=0;i<WORDS;i++)if(p[i]!=word(i,gen))_exit(40);}
static struct worker spawn(const char*group,int id) {
    int a[2],b[2];if(pipe(a)||pipe(b))die("pipe");pid_t pid=fork();if(pid<0)die("fork");
    if(!pid) {
        close(a[1]);close(b[0]);char path[128];snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/cgroup.procs",group);put(path,"0");
        cpu_set_t cpu;CPU_ZERO(&cpu);CPU_SET(id<0?0:id,&cpu);if(sched_setaffinity(0,sizeof(cpu),&cpu))_exit(41);
        unsigned gen=1;uint64_t*p=allocbuf(gen);uintptr_t addr=(uintptr_t)p;
        if(write(b[1],&addr,sizeof(addr))!=sizeof(addr))_exit(42);
        for(;;) {char c;if(read(a[0],&c,1)!=1)_exit(43);
            if(c=='X'){check(p,gen);munmap(p,16*MIB);_exit(0);}
            if(c=='W') {
                do {
                    for(size_t page=0;page<4096;page++) {
                        for(size_t j=0;j<512;j++){size_t i=page*512+j;if(p[i]!=word(i,gen))_exit(44);p[i]=word(i,gen+1);}
                        atomic_fetch_add(&shared->progress[id],1);
                    }
                    gen++;
                }while(!atomic_load(&shared->stop));
            } else if(c=='R') {check(p,gen);if(munmap(p,16*MIB))_exit(45);p=allocbuf(++gen);}
            else if(c!='V')_exit(46);
            check(p,gen);addr=(uintptr_t)p;if(write(b[1],&addr,sizeof(addr))!=sizeof(addr))_exit(47);
        }
    }
    close(a[0]);close(b[1]);struct worker w={pid,a[1],b[0],0};if(read(w.reply,&w.address,sizeof(w.address))!=sizeof(w.address))die("startup");return w;
}
static void send(struct worker*w,char c) {if(write(w->cmd,&c,1)!=1)die("command");}
static void ack(struct worker*w) {if(read(w->reply,&w->address,sizeof(w->address))!=sizeof(w->address))die("worker response");}
static void pages(struct worker*w,int round) {
    char p[80];snprintf(p,sizeof(p),"/proc/%d/pagemap",w->pid);int f=open(p,O_RDONLY);if(f<0)die("pagemap");int present=0,swapped=0;
    for(int i=0;i<4096;i++){uint64_t e;if(pread(f,&e,8,(w->address/4096+i)*8)!=8)die("page read");present+=!!(e&(1ULL<<63));swapped+=!!(e&(1ULL<<62));}close(f);
    printf("NEIGHBOR round=%d present=%d swapped=%d\n",round,present,swapped);if(present!=4096 || swapped)_exit(48);
}
static void snap(const char*stage,int round) {
    const char*names[]={"memory.current","memory.peak","memory.swap.current","memory.zswap.current","memory.max","memory.swap.max","memory.zswap.max","memory.oom.group","memory.cram.reserved"};char p[128];
    for(int i=0;i<9;i++){snprintf(p,sizeof(p),"/sys/fs/cgroup/budget/target/%s",names[i]);printf("STAT stage=%s round=%d key=%s value=%llu\n",stage,round,names[i],num(p));}
    const char*files[]={"memory.stat","memory.events"};
    for(int i=0;i<2;i++){snprintf(p,sizeof(p),"/sys/fs/cgroup/budget/target/%s",files[i]);FILE*f=fopen(p,"r");char k[128];unsigned long long n;if(!f)die(p);while(fscanf(f,"%127s %llu",k,&n)==2)printf("STAT stage=%s round=%d key=%s value=%llu\n",stage,round,k,n);fclose(f);}
    const char *arena[]={"arena_backing_bytes","arena_overhead_bytes","arena_used_bytes","arena_peak_bytes","arena_entries","arena_stores","arena_loads","arena_reject_full","arena_reject_quota","arena_reject_poor","arena_injected_failures"};
    for(unsigned i=0;i<sizeof(arena)/sizeof(arena[0]);i++){
        snprintf(p,sizeof(p),"/sys/kernel/debug/zswap/%s",arena[i]);
        printf("STAT stage=%s round=%d key=%s value=%llu\n",stage,round,arena[i],num(p));
    }
    printf("STAT stage=%s round=%d key=parent_reserved value=%llu\n",stage,round,num("/sys/fs/cgroup/budget/memory.cram.reserved"));
    printf("STAT stage=%s round=%d key=parent_limit value=%llu\n",stage,round,num("/sys/fs/cgroup/budget/memory.zswap.max"));
    printf("STAT stage=%s round=%d key=pool_bytes value=%llu\n",stage,round,num("/sys/kernel/debug/zswap/pool_total_size"));
    printf("STAT stage=%s round=%d key=stored_pages value=%llu\n",stage,round,num("/sys/kernel/debug/zswap/stored_pages"));
    FILE*f=fopen("/sys/block/vda/stat","r");unsigned long long v[11];if(!f)die("disk");for(int i=0;i<11;i++)if(fscanf(f,"%llu",v+i)!=1)die("disk stat");fclose(f);
    printf("IO stage=%s round=%d read=%llu written=%llu\n",stage,round,v[2],v[6]);fflush(stdout);
}
static void finish(struct worker*w) {send(w,'X');int s;if(waitpid(w->pid,&s,0)!=w->pid || !WIFEXITED(s) || WEXITSTATUS(s))die("worker exit");close(w->cmd);close(w->reply);}
static pid_t reclaim(int round,int id) {
    pid_t p=fork();if(p<0)die("fork reclaim");if(p)return p;
    for(int n=0;n<4;n++) {
        unsigned long before=atomic_load(&shared->progress[0])+atomic_load(&shared->progress[1]);double t=now();
        int f=open("/sys/fs/cgroup/budget/target/memory.reclaim",O_WRONLY);if(f<0)_exit(50);
        const char*s="2M swappiness=max";errno=0;ssize_t r=write(f,s,strlen(s));int e=errno;close(f);
        unsigned long after=atomic_load(&shared->progress[0])+atomic_load(&shared->progress[1]);
        printf("RECLAIM round=%d worker=%d request=%d result=%zd errno=%d seconds=%.6f progress=%lu\n",round,id,n,r,e,now()-t,after-before);fflush(stdout);
        if(r<0 && e!=EAGAIN)_exit(51);
    }_exit(0);
}
int main(int argc,char**argv) {
    if(argc!=3)return 2;
    int rounds=atoi(argv[2]);if(rounds<1 || rounds>1000)return 2;setvbuf(stdout,NULL,_IOLBF,0);
    shared=mmap(NULL,sizeof(*shared),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);if(shared==MAP_FAILED)die("shared");
    struct worker neighbor=spawn("neighbor",-1);
    if(!strcmp(argv[1],"oom")) {
        pid_t victim=fork();if(victim<0)die("oom fork");if(!victim){put("/sys/fs/cgroup/budget/target/cgroup.procs","0");volatile char*p=mmap(NULL,64*MIB,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);if(p==MAP_FAILED)_exit(60);for(size_t i=0;i<64*MIB;i+=4096)p[i]=1;_exit(61);}
        int s;if(waitpid(victim,&s,0)!=victim || !WIFSIGNALED(s) || WTERMSIG(s)!=SIGKILL)return 62;
        snap("oom",0);pages(&neighbor,0);send(&neighbor,'V');ack(&neighbor);finish(&neighbor);puts("OOM_CONTAINMENT_PASS");return 0;
    }
    struct worker workers[2]={spawn("budget/target",0),spawn("budget/target",1)};
    int prep=open("/sys/fs/cgroup/budget/target/memory.reclaim",O_WRONLY);if(prep<0)die("prepare reclaim");
    const char *request="32M swappiness=max";errno=0;ssize_t result=write(prep,request,strlen(request));int error=errno;close(prep);
    if(result<0 && error!=EAGAIN)die("prepare write");
    printf("PREPARE result=%zd errno=%d\n",result,error);
    snap("filled",0);pages(&neighbor,0);
    const char *groups[]={"budget","budget/target"};
    for(int i=0;i<2;i++) {
        char path[160];snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/memory.cram.reserved",groups[i]);
        if(num(path)) {
            snprintf(path,sizeof(path),"/sys/fs/cgroup/%s/memory.zswap.max",groups[i]);
            int f=open(path,O_WRONLY);if(f<0)die("quota write");errno=0;ssize_t r=write(f,"0",1);int e=errno;close(f);
            printf("LOWER_QUOTA group=%s result=%zd errno=%d\n",groups[i],r,e);
            if(r>=0 || e!=EBUSY)return 65;
        }
    }
    if(!strcmp(argv[1],"swapoff")) {
        if(system("/bin/busybox swapoff /dev/vda"))return 66;
        if(num("/sys/kernel/debug/zswap/arena_entries") || num("/sys/fs/cgroup/budget/target/memory.cram.reserved"))return 67;
        for(int i=0;i<2;i++)send(&workers[i],'V');
        for(int i=0;i<2;i++)ack(&workers[i]);
        pages(&neighbor,0);send(&neighbor,'V');ack(&neighbor);
        puts("LIVE_SWAPOFF_PASS");
        if(system("/bin/busybox swapon /dev/vda"))return 68;
    }
    for(int round=1;round<=rounds;round++) {
        atomic_store(&shared->stop,0);for(int i=0;i<2;i++)send(&workers[i],'W');
        pid_t r[2]={reclaim(round,0),reclaim(round,1)};int done[2]={0,0};double start=now();
        unsigned long long maxmem=0,maxz=0,maxpool=0;unsigned samples=0;
        while(!done[0] || !done[1]) {
            unsigned long long m=num("/sys/fs/cgroup/budget/target/memory.current"),z=num("/sys/fs/cgroup/budget/target/memory.zswap.current"),pool=num("/sys/kernel/debug/zswap/pool_total_size");
            if(m>maxmem)maxmem=m;
            if(z>maxz)maxz=z;
            if(pool>maxpool)maxpool=pool;
            samples++;
            for(int i=0;i<2;i++)if(!done[i]){int s;pid_t q=waitpid(r[i],&s,WNOHANG);if(q<0)die("reclaimer wait");if(q){if(!WIFEXITED(s)||WEXITSTATUS(s))return 63;done[i]=1;}}
            if(now()-start>60)return 64;
            usleep(20000);
        }
        atomic_store(&shared->stop,1);for(int i=0;i<2;i++)ack(&workers[i]);
        pages(&neighbor,round);send(&neighbor,'V');ack(&neighbor);
        printf("ROUND round=%d samples=%u peak_sample_memory=%llu peak_sample_zswap=%llu peak_sample_pool=%llu pages0=%lu pages1=%lu seconds=%.6f\n",round,samples,maxmem,maxz,maxpool,atomic_load(&shared->progress[0]),atomic_load(&shared->progress[1]),now()-start);
        snap("round",round);
        if(round%4==0){for(int i=0;i<2;i++)send(&workers[i],'R');for(int i=0;i<2;i++)ack(&workers[i]);printf("REMAP round=%d PASS\n",round);}
    }
    for(int i=0;i<2;i++)finish(&workers[i]);
    finish(&neighbor);sleep(3);snap("released",rounds);
    put("/sys/fs/cgroup/budget/target/memory.zswap.max","0");put("/sys/fs/cgroup/budget/memory.zswap.max","0");
    puts("EMPTY_QUOTA_LOWER_PASS");puts("CHURN_INTEGRITY_PASS");return 0;
}
