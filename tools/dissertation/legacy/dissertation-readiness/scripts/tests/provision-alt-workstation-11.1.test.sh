#!/usr/bin/env bash
# Behavioural tests use dry-run-only fixtures; they never provision the host.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$SCRIPT_DIR/provision-alt-workstation-11.1.sh"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# The implementation distinction is contractual: do not permit the other
# ActiveMQ family to leak into this runbook or its provisioning artifacts.
FORBIDDEN_IMPLEMENTATION=$(printf 'Arte%smis' '')
TERM_FILES=("$SCRIPT" "$SCRIPT_DIR/tests" "$SCRIPT_DIR/../four-broker-stand-alt-workstation-11.1.md")
if command -v rg >/dev/null 2>&1; then
  term_found=0
  rg -qi "$FORBIDDEN_IMPLEMENTATION" "${TERM_FILES[@]}" && term_found=1
elif command -v grep >/dev/null 2>&1; then
  term_found=0
  grep -Rqi "$FORBIDDEN_IMPLEMENTATION" "${TERM_FILES[@]}" && term_found=1
else
  printf 'terminology gate requires rg or grep\n' >&2
  exit 2
fi
if (( term_found )); then
  printf 'forbidden ActiveMQ implementation reference found\n' >&2
  exit 1
fi

cat >"$TMP_DIR/os-release" <<'EOF'
ID=altlinux
PRETTY_NAME="ALT Workstation 11.1"
VERSION_ID=11.1
ALT_BRANCH_ID=p11
EOF
cat >"$TMP_DIR/lscpu.csv" <<'EOF'
# CPU,Core,Socket,Online
0,0,0,Y
4,0,0,Y
1,1,0,Y
5,1,0,Y
2,2,0,Y
6,2,0,Y
3,3,0,Y
7,3,0,Y
8,4,0,N
EOF
cat >"$TMP_DIR/meminfo" <<'EOF'
MemTotal:        16777216 kB
EOF

MOCK_BIN="$TMP_DIR/bin"
mkdir "$MOCK_BIN"
cat >"$MOCK_BIN/uname" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  -s) printf 'Linux\n' ;;
  -m) printf 'x86_64\n' ;;
  -r) printf 'fixture-kernel\n' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$MOCK_BIN/uname"
cat >"$MOCK_BIN/timedatectl" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$MOCK_BIN/timedatectl"
cat >"$MOCK_BIN/chronyc" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$MOCK_BIN/chronyc"
for command_name in nft systemctl; do
  cat >"$MOCK_BIN/$command_name" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "$MOCK_BIN/$command_name"
done

base_env=(
  "PATH=$MOCK_BIN:$PATH"
  "STAND_TEST_OS_RELEASE=$TMP_DIR/os-release"
  "STAND_TEST_CPU_TOPOLOGY=$TMP_DIR/lscpu.csv"
  "STAND_TEST_MEMINFO=$TMP_DIR/meminfo"
  "STAND_TEST_ROOT_SSD=yes"
  "STAND_TEST_NETWORK_INTERFACE=enp1s0"
  "STAND_TEST_NETWORK_TYPE=wired"
  "STAND_TEST_NETWORK_SPEED=1000Mb/s"
)

run_fixture() {
  env "${base_env[@]}" "$SCRIPT" "$@"
}

expect_failure() {
  local expected=$1
  shift
  local output
  if output=$(run_fixture "$@" 2>&1); then
    printf 'expected failure for: %s\n' "$*" >&2
    exit 1
  fi
  [[ "$output" == *"$expected"* ]] || {
    printf 'failure did not contain %q: %s\n' "$expected" "$output" >&2
    exit 1
  }
}

manifest=$(run_fixture)
for expected in \
  '"altBranchId":"p11"' \
  '"versionId":"11.1"' \
  '"altP11_11_1":true' \
  '"physicalCores":"4"' \
  '"brokerCpuset":"3"' \
  '"reservedCpuset":"0,1,2,4,5,6"' \
  '"networkType":"wired"' \
  '"benchmarkNetworkGo":true' \
  '"cpuTopology":[{"cpu":0,"core":0,"socket":0}'; do
  grep -Fq "$expected" <<<"$manifest" || { printf 'missing manifest field: %s\n' "$expected" >&2; exit 1; }
done
printf 'ALT/SMT fixture manifest: OK\n'

expect_failure "exactly one online CPU" --broker-cpuset 3-4
expect_failure "not an online CPU" --broker-cpuset 99
expect_failure "not an online CPU" --broker-cpuset 8
expect_failure "shares physical core" --broker-cpuset 3 --reserved-cpuset 7
expect_failure "HTTPS URL" --registry-mirror http://mirror.example.org
expect_failure "without userinfo, path, query or fragment" --registry-mirror 'https://user@mirror.example.org/v2?token=x#fragment'

host_all_apply_manifest=$(env "${base_env[@]}" STAND_TEST_NO_APPLY=1 "$SCRIPT" --apply --mode hostAllCores)
grep -Fq '"applyRequested":true' <<<"$host_all_apply_manifest"
grep -Fq '"mode":"hostAllCores"' <<<"$host_all_apply_manifest"
grep -Fq '"brokerCpuset":""' <<<"$host_all_apply_manifest"
printf 'hostAllCores apply preflight without broker cpuset: OK\n'

wireless_env=("${base_env[@]}" "STAND_TEST_NETWORK_TYPE=wireless" "STAND_TEST_NETWORK_SPEED=1000Mb/s")
wireless_manifest=$(env "${wireless_env[@]}" "$SCRIPT" 2>"$TMP_DIR/wireless.stderr")
grep -Fq '"networkType":"wireless"' <<<"$wireless_manifest"
grep -Fq '"benchmarkNetworkGo":false' <<<"$wireless_manifest"
printf 'wireless network gate: OK\n'
grep -Fq "wireless" "$TMP_DIR/wireless.stderr"

grep -Fq '@DATA_IP@' "$SCRIPT_DIR/../four-broker-stand-alt-workstation-11.1.md"
grep -Fq 'sha256sum' "$SCRIPT_DIR/../four-broker-stand-alt-workstation-11.1.md"

printf 'all provisioning dry-run fixture tests: OK\n'
