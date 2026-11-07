/* Used under allow/deny profiles in the disposable guest only. */
#include <sys/socket.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    (void)argv;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (argc > 1) {
        if (fd < 0 && errno == EACCES) {
            puts("APPARMOR_NETWORK_DENY=PASS");
            return 0;
        }
        if (fd >= 0) close(fd);
        return 1;
    }
    if (fd < 0) { perror("socket"); return 1; }
    close(fd);
    puts("APPARMOR_NETWORK_ALLOW=PASS");
    return 0;
}
