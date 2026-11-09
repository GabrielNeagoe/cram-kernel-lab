# Run inside a dedicated trial boot. The existing trial enforces the workload
# budget, four online CPUs, compression settings, swap-I/O and arena limits.
set -eu
before=$(cat /proc/sys/vm/page-cluster)
test "$before" = 3
restore() {
    sysctl -q -w "vm.page-cluster=$before"
    test "$(cat /proc/sys/vm/page-cluster)" = "$before"
}
trap restore EXIT
sysctl -q -w vm.page-cluster=0
test "$(cat /proc/sys/vm/page-cluster)" = 0
CRAM_HIGH_NET=0 python3 trial.py spill
