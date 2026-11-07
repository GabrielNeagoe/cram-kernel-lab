/* Notification protocol checks for a disposable guest, never the host. */
#include <sys/types.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <linux/apparmor.h>

static int prompt_round(int fd, int allow)
{
    const char *path = allow ? "/prompt-allow" : "/prompt-deny";
    pid_t child = fork();
    if (child < 0) return 10;
    if (!child) {
        close(fd);
        execl("/usr/bin/aa-exec", "aa-exec", "-p", "cram-aa-prompt", "--",
              "/bin/busybox", "cat", path, (char *)NULL);
        _exit(127);
    }
    int status, result = 11;
    struct pollfd p = {.fd = fd, .events = POLLIN};
    if (poll(&p, 1, 5000) != 1 || !(p.revents & POLLIN)) goto cleanup;
    unsigned char buffer[4096] = {0};
    struct apparmor_notif_file_v5 *event = (void *)buffer;
    event->base.base.base.len = sizeof(buffer);
    event->base.base.base.version = APPARMOR_NOTIFY_V5;
    int n = ioctl(fd, APPARMOR_NOTIF_RECV, buffer);
    fprintf(stderr, "prompt recv=%d pid=%d expected=%d offset=%u allow=%x deny=%x\n",
            n, event->base.pid, child, event->name, event->base.allow, event->base.deny);
    if (n > 0 && event->name < (unsigned)n &&
        memchr(buffer + event->name, 0, n - event->name))
        fprintf(stderr, "prompt path=%s\n", buffer + event->name);
    if (n < (int)sizeof(*event) || event->base.pid != child ||
        event->name >= (unsigned)n ||
        !memchr(buffer + event->name, 0, n - event->name) ||
        strcmp((char *)buffer + event->name, path) ||
        !event->base.deny) goto cleanup;
    struct apparmor_notif_resp_perm response = {0};
    response.base.base.len = sizeof(response);
    response.base.base.version = APPARMOR_NOTIFY_V5;
    response.base.ntype = APPARMOR_NOTIF_RESP_PERM;
    response.base.flags = URESPONSE_NO_CACHE;
    response.base.id = event->base.base.id;
    response.allow = allow ? event->base.allow | event->base.deny : event->base.allow;
    response.deny = allow ? 0 : event->base.deny;
    if (ioctl(fd, APPARMOR_NOTIF_SEND, &response) != sizeof(response)) goto cleanup;
    for (int i = 0; i < 500; i++) {
        if (waitpid(child, &status, WNOHANG) == child) {
            if (!WIFEXITED(status) || WEXITSTATUS(status) != (allow ? 0 : 1)) return 12;
            printf("NOTIFY_PROMPT_%s=PASS\n", allow ? "ALLOW" : "DENY");
            return 0;
        }
        usleep(10000);
    }
cleanup:
    perror("prompt round");
    close(fd);
    kill(child, SIGKILL);
    waitpid(child, &status, 0);
    return result;
}

int main(int argc, char **argv)
{
    (void)argv;
    int fd = open("/sys/kernel/security/apparmor/.notify", O_RDWR);
    if (fd < 0) { perror("notify open"); return 1; }
    for (int version = 3; version <= 5; version += 2) {
        union apparmor_notif_filters f = {0};
        f.base.len = sizeof(f);
        f.base.version = version;
        f.modeset = APPARMOR_MODESET_USER;
        if (ioctl(fd, APPARMOR_NOTIF_SET_FILTER, &f) != sizeof(f)) {
            perror("set filter"); return 2;
        }
        printf("NOTIFY_FILTER_V%d=PASS\n", version);
    }
    union apparmor_notif_filters bad = {0};
    bad.base.len = sizeof(bad);
    bad.base.version = 99;
    errno = 0;
    if (ioctl(fd, APPARMOR_NOTIF_SET_FILTER, &bad) != -1 ||
        errno != EPROTONOSUPPORT) return 3;
    puts("NOTIFY_INVALID_VERSION=PASS");
    struct apparmor_notif_register_v5 reg = {
        .base = {sizeof(reg), APPARMOR_NOTIFY_V5}
    };
    if (ioctl(fd, APPARMOR_NOTIF_REGISTER, &reg) != sizeof(reg) ||
        !reg.listener_id) { perror("register"); return 4; }
    puts("NOTIFY_REGISTER=PASS");
    struct apparmor_notif_resend_v5 resend = {
        .base = {sizeof(resend), APPARMOR_NOTIFY_V5},
        .listener_id = reg.listener_id
    };
    if (ioctl(fd, APPARMOR_NOTIF_RESEND, &resend) != sizeof(resend) ||
        resend.ready || resend.pending) { perror("resend"); return 5; }
    puts("NOTIFY_EMPTY_RESEND=PASS");
    if (argc > 1) {
        int result = prompt_round(fd, 1);
        if (!result) result = prompt_round(fd, 0);
        if (result) return result;
    }
    close(fd);
    return 0;
}
