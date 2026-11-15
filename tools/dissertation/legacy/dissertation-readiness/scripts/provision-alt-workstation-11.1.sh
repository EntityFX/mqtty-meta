#!/usr/bin/env bash
# Provision a four-broker benchmark host running ALT Workstation 11.1 (p11).
# Safe by default: without --apply this script only inspects and prints JSON.
set -euo pipefail

APPLY=0
MODE="singleCorePinned"
ALLOW_NETWORK_PROVISIONING=0
DOCKER_USER=""
BROKER_CPUSET=""
RESERVED_CPUSET=""
REGISTRY_MIRROR=""

# Test fixtures are accepted only by dry-run; production --apply never trusts
# environment-supplied hardware or OS facts.
TEST_OS_RELEASE=${STAND_TEST_OS_RELEASE:-}
TEST_CPU_TOPOLOGY=${STAND_TEST_CPU_TOPOLOGY:-}
TEST_MEMINFO=${STAND_TEST_MEMINFO:-}
TEST_ROOT_SSD=${STAND_TEST_ROOT_SSD:-}
TEST_NETWORK_INTERFACE=${STAND_TEST_NETWORK_INTERFACE:-}
TEST_NETWORK_TYPE=${STAND_TEST_NETWORK_TYPE:-}
TEST_NETWORK_SPEED=${STAND_TEST_NETWORK_SPEED:-}
TEST_NO_APPLY=${STAND_TEST_NO_APPLY:-}

usage() {
    cat <<'USAGE'
Usage: provision-alt-workstation-11.1.sh [options]

Default is dry-run: inspect the host and print one JSON manifest to stdout.

  --apply                       Install/enable packages after all safety checks.
  --docker-user USER            Optionally add an existing USER to docker group.
  --mode MODE                   singleCorePinned (default) or hostAllCores.
  --broker-cpuset CPU           One online CPU for singleCorePinned (for example: 6).
  --reserved-cpuset CPUSET      CPUs reserved for OS/telemetry/NIC IRQ/RPS.
  --registry-mirror HTTPS-URL   Explicit unauthenticated registry mirror only.
  --allow-network-provisioning  Allow provisioning actions on a benchmark-no-go
                                network; never changes benchmark status.
  -h, --help                    Show this help.

No password, token, credential-bearing URL, firewall, SSH, CPU governor,
Turbo, IRQ or RPS setting is accepted or changed by this script.
USAGE
}

log() { printf '%s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

run() {
    if (( APPLY )); then
        log "+ $*"
        "$@" >&2
    else
        log "[dry-run] $*"
    fi
}

is_cpuset() {
    [[ "$1" =~ ^[0-9]+([,-][0-9]+)*$ ]]
}

is_safe_registry_mirror() {
    # A mirror is an endpoint, not a repository path. Disallow every URL form
    # that can carry a secret (userinfo, query or fragment) as well as HTTP.
    [[ "$1" =~ ^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]]
}

json_escape() {
    local value=${1//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    value=${value//$'\t'/\\t}
    printf '%s' "$value"
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

os_value() {
    local file=$1 key=$2
    awk -v wanted="$key" '
        index($0, wanted "=") == 1 {
            value = substr($0, length(wanted) + 2)
            sub(/^"/, "", value); sub(/"$/, "", value)
            print value; exit
        }
    ' "$file" 2>/dev/null
}

read_os_release() {
    local file=/etc/os-release
    if [[ -n "$TEST_OS_RELEASE" ]]; then
        file=$TEST_OS_RELEASE
    fi
    os_id="unknown"
    os_pretty="unknown"
    os_version_id="unknown"
    os_alt_branch_id="unknown"
    alt_p11_11_1=false
    [[ -r "$file" ]] || return 0
    os_id=$(os_value "$file" ID); os_id=${os_id:-unknown}
    os_pretty=$(os_value "$file" PRETTY_NAME); os_pretty=${os_pretty:-unknown}
    os_version_id=$(os_value "$file" VERSION_ID); os_version_id=${os_version_id:-unknown}
    os_alt_branch_id=$(os_value "$file" ALT_BRANCH_ID); os_alt_branch_id=${os_alt_branch_id:-unknown}
    if [[ "$os_id" == "altlinux" && "$os_alt_branch_id" == "p11" && "$os_version_id" == "11.1" ]]; then
        alt_p11_11_1=true
    else
        alt_p11_11_1=false
    fi
}

cpu_topology_input() {
    if [[ -n "$TEST_CPU_TOPOLOGY" ]]; then
        cat "$TEST_CPU_TOPOLOGY"
    else
        lscpu -p=CPU,CORE,SOCKET,ONLINE 2>/dev/null
    fi
}

expand_cpuset() {
    local set=$1 item start end cpu
    IFS=, read -r -a cpuset_items <<<"$set"
    for item in "${cpuset_items[@]}"; do
        if [[ "$item" == *-* ]]; then
            start=${item%-*}
            end=${item#*-}
            (( 10#$start <= 10#$end )) || return 1
            for (( cpu = 10#$start; cpu <= 10#$end; cpu++ )); do
                printf '%s\n' "$cpu"
            done
        else
            printf '%s\n' "$item"
        fi
    done
}

validate_cpu_assignments() {
    local cpu expanded
    [[ "$MODE" == "singleCorePinned" ]] || return 0
    [[ "$BROKER_CPUSET" =~ ^[0-9]+$ ]] || die "singleCorePinned requires exactly one online CPU in --broker-cpuset"
    if (( ${#online_cpu[@]} == 0 )); then
        (( APPLY )) && die "cannot validate --broker-cpuset without online CPU topology"
        return 0
    fi
    [[ -n "${online_cpu[$BROKER_CPUSET]:-}" ]] || die "--broker-cpuset $BROKER_CPUSET is not an online CPU"
    [[ -n "${cpu_core_socket[$BROKER_CPUSET]:-}" ]] || die "cannot map --broker-cpuset $BROKER_CPUSET to a physical core"
    [[ -n "$RESERVED_CPUSET" ]] || return 0
    expanded=$(expand_cpuset "$RESERVED_CPUSET") || die "--reserved-cpuset has an invalid range"
    while IFS= read -r cpu; do
        [[ -n "${online_cpu[$cpu]:-}" ]] || die "--reserved-cpuset CPU $cpu is not an online CPU"
        [[ "$cpu" != "$BROKER_CPUSET" ]] || die "--reserved-cpuset overlaps reserved broker CPU $BROKER_CPUSET"
        [[ "${cpu_core_socket[$cpu]}" != "${cpu_core_socket[$BROKER_CPUSET]}" ]] || \
            die "--reserved-cpuset CPU $cpu shares physical core with broker CPU $BROKER_CPUSET"
    done <<<"$expanded"
}

refresh_ntp_state() {
    if command_exists timedatectl && timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes; then
        ntp_state="synchronised"
    elif command_exists chronyc && chronyc tracking 2>/dev/null | grep -qi 'Leap status.*Normal'; then
        ntp_state="synchronised"
    else
        ntp_state="not-confirmed"
    fi
}

enable_time_service() {
    local unit
    for unit in chronyd chrony systemd-timesyncd; do
        if systemctl cat "$unit.service" >/dev/null 2>&1; then
            run systemctl enable --now "$unit.service"
            return 0
        fi
    done
    die "no supported NTP service unit was installed"
}

linux=0
if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
    linux=1
fi

while (( $# )); do
    case "$1" in
        --apply) APPLY=1 ;;
        --mode)
            (( $# >= 2 )) || die "--mode needs a value"
            MODE=$2
            shift
            ;;
        --allow-network-provisioning) ALLOW_NETWORK_PROVISIONING=1 ;;
        --docker-user)
            (( $# >= 2 )) || die "--docker-user needs a value"
            DOCKER_USER=$2
            shift
            ;;
        --broker-cpuset)
            (( $# >= 2 )) || die "--broker-cpuset needs a value"
            BROKER_CPUSET=$2
            shift
            ;;
        --reserved-cpuset)
            (( $# >= 2 )) || die "--reserved-cpuset needs a value"
            RESERVED_CPUSET=$2
            shift
            ;;
        --registry-mirror)
            (( $# >= 2 )) || die "--registry-mirror needs a value"
            REGISTRY_MIRROR=$2
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

[[ "$MODE" == "singleCorePinned" || "$MODE" == "hostAllCores" ]] || \
    die "--mode must be singleCorePinned or hostAllCores"
if [[ -n "$BROKER_CPUSET" ]] && [[ ! "$BROKER_CPUSET" =~ ^[0-9]+$ ]]; then
    die "singleCorePinned requires exactly one online CPU in --broker-cpuset"
fi
if [[ -n "$RESERVED_CPUSET" ]] && ! is_cpuset "$RESERVED_CPUSET"; then
    die "--reserved-cpuset must contain only CPU numbers, commas and ranges"
fi
if [[ -n "$REGISTRY_MIRROR" ]]; then
    is_safe_registry_mirror "$REGISTRY_MIRROR" || \
        die "--registry-mirror must be an HTTPS URL without userinfo, path, query or fragment"
fi
if (( APPLY )) && [[ -n "$TEST_OS_RELEASE$TEST_CPU_TOPOLOGY$TEST_MEMINFO$TEST_ROOT_SSD$TEST_NETWORK_INTERFACE$TEST_NETWORK_TYPE$TEST_NETWORK_SPEED" ]] && [[ "$TEST_NO_APPLY" != 1 ]]; then
    die "STAND_TEST_* fixtures are dry-run only and cannot be used with --apply"
fi
if [[ -n "$TEST_NO_APPLY" ]] && [[ -z "$TEST_OS_RELEASE$TEST_CPU_TOPOLOGY$TEST_MEMINFO$TEST_ROOT_SSD$TEST_NETWORK_INTERFACE$TEST_NETWORK_TYPE$TEST_NETWORK_SPEED" ]]; then
    die "STAND_TEST_NO_APPLY requires STAND_TEST_* fixtures"
fi

architecture="$(uname -m 2>/dev/null || printf unknown)"
hostname_value="$(hostname 2>/dev/null || printf unknown)"
kernel="$(uname -r 2>/dev/null || printf unknown)"
physical_cores="unknown"
memory_kib="unknown"
root_ssd="unknown"
ntp_state="unknown"
firewall_state="unknown"
apt_state="absent"
physical_cpu_candidates=""
suggested_broker_cpuset=""
suggested_reserved_cpuset=""
network_interface=""
network_type="unknown"
network_speed="unknown"
network_speed_mbps="unknown"
benchmark_network_go=false
cpu_topology_json=""
declare -A online_cpu=()
declare -A cpu_core_socket=()
declare -a online_cpu_order=()
declare -a topology_json_entries=()
declare -a physical_cpus=()
declare -a reserved_cpus=()

read_os_release

if (( linux )); then
    command_exists apt-get && apt_state="present"

    if [[ -n "$TEST_CPU_TOPOLOGY" || $(command_exists lscpu && printf yes || printf no) == yes ]]; then
        # Keep every online logical CPU. The first CPU for each socket/core pair
        # is a physical-core candidate; the other sibling(s) remain visible.
        declare -A seen_physical=()
        while IFS=, read -r cpu core socket; do
            [[ "$cpu" =~ ^[0-9]+$ && "$core" =~ ^[0-9]+$ && "$socket" =~ ^[0-9]+$ ]] || continue
            online_cpu[$cpu]=1
            cpu_core_socket[$cpu]="$core:$socket"
            online_cpu_order+=("$cpu")
            topology_json_entries+=("{\"cpu\":$cpu,\"core\":$core,\"socket\":$socket}")
            if [[ -z "${seen_physical[$core:$socket]:-}" ]]; then
                seen_physical[$core:$socket]=1
                physical_cpus+=("$cpu")
            fi
        done < <(cpu_topology_input | awk -F, '/^#/ { next } $4 == "Y" { print $1 "," $2 "," $3 }' | sort -t, -k1,1n)
        if (( ${#physical_cpus[@]} )); then
            physical_cores=${#physical_cpus[@]}
            physical_cpu_candidates=$(IFS=,; printf '%s' "${physical_cpus[*]}")
            # Select a physical-core candidate, never a fixed CPU number.
            suggested_broker_cpuset=${physical_cpus[${#physical_cpus[@]}-1]}
            selected_key=${cpu_core_socket[$suggested_broker_cpuset]}
            for cpu in "${online_cpu_order[@]}"; do
                [[ "${cpu_core_socket[$cpu]}" == "$selected_key" ]] || reserved_cpus+=("$cpu")
            done
            suggested_reserved_cpuset=$(IFS=,; printf '%s' "${reserved_cpus[*]:-}")
        fi
        cpu_topology_json=$(IFS=,; printf '%s' "${topology_json_entries[*]:-}")
    fi

    meminfo_file=/proc/meminfo
    [[ -n "$TEST_MEMINFO" ]] && meminfo_file=$TEST_MEMINFO
    if [[ -r "$meminfo_file" ]]; then
        memory_kib=$(awk '/^MemTotal:/ { print $2; exit }' "$meminfo_file")
    fi

    if [[ -n "$TEST_ROOT_SSD" ]]; then
        root_ssd=$TEST_ROOT_SSD
    elif command_exists findmnt && command_exists lsblk; then
        root_source=$(findmnt -n -o SOURCE / 2>/dev/null || true)
        root_device=${root_source%%\[*}
        root_name=$(lsblk -ndo PKNAME "$root_device" 2>/dev/null | head -n 1 || true)
        [[ -n "$root_name" ]] || root_name=$(basename "$root_device")
        root_rota=$(lsblk -ndo ROTA "/dev/$root_name" 2>/dev/null | head -n 1 || true)
        case "$root_rota" in
            0) root_ssd="yes" ;;
            1) root_ssd="no" ;;
            *) root_ssd="unknown" ;;
        esac
    fi

    refresh_ntp_state

    if command_exists nft && nft list ruleset >/dev/null 2>&1; then
        firewall_state="nftables-ruleset-readable"
    elif command_exists systemctl && systemctl is-active --quiet firewalld; then
        firewall_state="firewalld-active"
    else
        firewall_state="not-confirmed"
    fi

    if [[ -n "$TEST_NETWORK_INTERFACE" ]]; then
        network_interface=$TEST_NETWORK_INTERFACE
        network_type=${TEST_NETWORK_TYPE:-unknown}
        network_speed=${TEST_NETWORK_SPEED:-unknown}
    elif command_exists ip; then
        network_interface=$(ip -o route show default 2>/dev/null | awk '{print $5; exit}' || true)
        if [[ -n "$network_interface" && -d "/sys/class/net/$network_interface/wireless" ]]; then
            network_type="wireless"
        elif [[ -n "$network_interface" ]] && command_exists iw && iw dev "$network_interface" info >/dev/null 2>&1; then
            network_type="wireless"
        elif [[ -n "$network_interface" ]] && command_exists ethtool; then
            network_type="wired"
            network_speed=$(ethtool "$network_interface" 2>/dev/null | awk -F': ' '/^[[:space:]]*Speed:/ { print $2; exit }' || printf unknown)
        fi
    fi
    if [[ "$network_speed" =~ ^([0-9]+)Mb/s$ ]]; then
        network_speed_mbps=${BASH_REMATCH[1]}
    fi
    if [[ "$network_type" == "wired" && "$network_speed_mbps" =~ ^[0-9]+$ && "$network_speed_mbps" -ge 1000 ]]; then
        benchmark_network_go=true
    fi
fi

if [[ "$MODE" == "singleCorePinned" ]]; then
    [[ -n "$BROKER_CPUSET" ]] || BROKER_CPUSET=$suggested_broker_cpuset
    [[ -n "$RESERVED_CPUSET" ]] || RESERVED_CPUSET=$suggested_reserved_cpuset
else
    [[ -z "$BROKER_CPUSET$RESERVED_CPUSET" ]] || die "hostAllCores does not accept broker or reserved cpusets"
fi
validate_cpu_assignments

emit_manifest() {
    printf '{'
    printf '"schemaVersion":1,'
    printf '"hostname":"%s",' "$(json_escape "$hostname_value")"
    printf '"os":"%s",' "$(json_escape "$os_pretty")"
    printf '"versionId":"%s",' "$(json_escape "$os_version_id")"
    printf '"altBranchId":"%s",' "$(json_escape "$os_alt_branch_id")"
    printf '"altP11_11_1":%s,' "$alt_p11_11_1"
    printf '"kernel":"%s",' "$(json_escape "$kernel")"
    printf '"architecture":"%s",' "$(json_escape "$architecture")"
    printf '"physicalCores":"%s",' "$(json_escape "$physical_cores")"
    printf '"memoryKiB":"%s",' "$(json_escape "$memory_kib")"
    printf '"rootSsd":"%s",' "$(json_escape "$root_ssd")"
    printf '"ntp":"%s",' "$(json_escape "$ntp_state")"
    printf '"firewall":"%s",' "$(json_escape "$firewall_state")"
    printf '"apt":"%s",' "$(json_escape "$apt_state")"
    printf '"networkInterface":"%s",' "$(json_escape "$network_interface")"
    printf '"networkType":"%s",' "$(json_escape "$network_type")"
    printf '"networkSpeed":"%s",' "$(json_escape "$network_speed")"
    printf '"benchmarkNetworkGo":%s,' "$benchmark_network_go"
    printf '"mode":"%s",' "$(json_escape "$MODE")"
    printf '"physicalCpuCandidates":"%s",' "$(json_escape "$physical_cpu_candidates")"
    printf '"cpuTopology":[%s],' "$cpu_topology_json"
    printf '"brokerCpuset":"%s",' "$(json_escape "$BROKER_CPUSET")"
    printf '"reservedCpuset":"%s",' "$(json_escape "$RESERVED_CPUSET")"
    printf '"applyRequested":%s' "$([[ $APPLY -eq 1 ]] && printf true || printf false)"
    printf '}\n'
}

if (( ! APPLY )); then
    [[ $linux -eq 1 ]] || warn "not Linux: dry-run is diagnostic only; --apply will refuse"
    [[ "$architecture" == "x86_64" ]] || warn "architecture is not x86_64"
    [[ "$physical_cores" =~ ^[0-9]+$ && "$physical_cores" -ge 4 ]] || warn "fewer than four physical cores, or topology unknown"
    [[ "$memory_kib" =~ ^[0-9]+$ && "$memory_kib" -ge 8388608 ]] || warn "less than 8 GiB nominal RAM, or value unknown"
    [[ "$root_ssd" == "yes" ]] || warn "root SSD is not confirmed"
    [[ "$ntp_state" == "synchronised" ]] || warn "NTP synchronisation is not confirmed"
    if [[ "$MODE" == "singleCorePinned" ]]; then
        [[ -n "$BROKER_CPUSET" ]] || warn "cannot derive broker cpuset; pass --broker-cpuset after inspecting lscpu"
    fi
    if [[ "$network_type" == "wireless" ]]; then
        warn "wireless network is benchmark no-go; use wired 1GbE or faster"
    elif [[ "$network_type" != "wired" ]]; then
        warn "wired network interface is not confirmed; benchmark no-go"
    elif [[ "$network_speed_mbps" == "unknown" ]]; then
        warn "wired network speed is unknown; benchmark no-go"
    elif (( network_speed_mbps < 1000 )); then
        warn "wired network speed is below 1GbE; benchmark no-go"
    fi
    emit_manifest
    exit 0
fi

(( linux )) || die "--apply is supported only on Linux"
[[ "$architecture" == "x86_64" ]] || die "requires x86_64, got $architecture"
[[ "$physical_cores" =~ ^[0-9]+$ && "$physical_cores" -ge 4 ]] || die "requires at least four detectable physical CPU cores"
[[ "$memory_kib" =~ ^[0-9]+$ && "$memory_kib" -ge 8388608 ]] || die "requires at least 8 GiB nominal RAM"
[[ "$root_ssd" == "yes" ]] || die "root SSD is not confirmed; inspect storage and rerun only if appropriate"
if [[ "$MODE" == "singleCorePinned" ]]; then
    [[ -n "$BROKER_CPUSET" ]] || die "cannot derive broker cpuset; pass --broker-cpuset"
fi

# Fixture-only apply preflight: exercise the same mode/OS/network gates without
# privilege or mutations. It is unreachable unless synthetic facts were supplied.
if [[ "$TEST_NO_APPLY" == 1 ]]; then
    [[ "$os_id" == "altlinux" ]] || die "--apply is restricted to ALT Linux"
    [[ "$os_alt_branch_id" == "p11" ]] || die "--apply requires ALT_BRANCH_ID=p11"
    [[ "$os_version_id" == "11.1" ]] || die "--apply requires VERSION_ID=11.1"
    [[ "$benchmark_network_go" == true ]] || die "fixture apply preflight requires wired >=1GbE"
    emit_manifest
    exit 0
fi
[[ $EUID -eq 0 ]] || die "--apply requires root (use sudo)"
command_exists apt-get || die "apt-get is required"

[[ "$os_id" == "altlinux" ]] || die "--apply is restricted to ALT Linux"
[[ "$os_alt_branch_id" == "p11" ]] || die "--apply requires ALT_BRANCH_ID=p11"
[[ "$os_version_id" == "11.1" ]] || die "--apply requires VERSION_ID=11.1"
if [[ "$benchmark_network_go" != true ]]; then
    (( ALLOW_NETWORK_PROVISIONING )) || die "wired >=1GbE network is required; --allow-network-provisioning permits provisioning only, never a benchmark"
    warn "network remains benchmark no-go; proceeding with provisioning only because --allow-network-provisioning was explicit"
fi

run apt-get update
run apt-get install -y docker-engine docker-compose-v2 chrony python3
run systemctl enable --now docker
enable_time_service

if [[ -n "$DOCKER_USER" ]]; then
    id "$DOCKER_USER" >/dev/null 2>&1 || die "requested docker user does not exist: $DOCKER_USER"
    getent group docker >/dev/null 2>&1 || run groupadd docker
    run gpasswd -a "$DOCKER_USER" docker
    log "User $DOCKER_USER must log out and back in before using docker without sudo."
fi

if [[ -n "$REGISTRY_MIRROR" ]]; then
    command_exists python3 || die "python3 is required to update daemon.json safely"
    log "Adding unauthenticated registry mirror to /etc/docker/daemon.json"
    python3 - "$REGISTRY_MIRROR" <<'PY' >&2
import json
import os
import sys
import tempfile

path = "/etc/docker/daemon.json"
mirror = sys.argv[1]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as source:
        data = json.load(source)
if not isinstance(data, dict):
    raise SystemExit("/etc/docker/daemon.json must contain a JSON object")
mirrors = data.get("registry-mirrors", [])
if not isinstance(mirrors, list) or not all(isinstance(item, str) for item in mirrors):
    raise SystemExit("registry-mirrors must be a JSON string array")
if mirror not in mirrors:
    mirrors.append(mirror)
data["registry-mirrors"] = mirrors
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix="daemon.json.", dir=directory, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        json.dump(data, target, indent=2, sort_keys=True)
        target.write("\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
    run systemctl restart docker
fi

command_exists docker || die "docker command is unavailable after installation"
docker compose version >&2 || die "Docker Compose v2 is unavailable after installation"
systemctl is-active --quiet docker || die "docker service is not active after installation"
refresh_ntp_state

emit_manifest
