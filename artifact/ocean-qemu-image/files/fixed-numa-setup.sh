#!/bin/bash

# Configure a guest's CXL Type-3 device as memory-only NUMA node 1.
# This is intentionally safe to run repeatedly and is designed to run after
# graphical.target so slow devdax page initialization cannot block login.

set -euo pipefail

LOG_FILE=${CXL_NUMA_LOG_FILE:-/var/log/cxl_numa_setup.log}
CMDLINE_FILE=${CXL_NUMA_CMDLINE_FILE:-/proc/cmdline}
SYS_DAX_DEVICES=${CXL_NUMA_SYS_DAX_DEVICES:-/sys/bus/dax/devices}
DEV_ROOT=${CXL_NUMA_DEV_ROOT:-/dev}
MAX_RETRIES=${CXL_NUMA_MAX_RETRIES:-10}
RETRY_DELAY=${CXL_NUMA_RETRY_DELAY:-2}
CXL_NUMA_MODE=${CXL_NUMA_MODE:-devdax}
# Ephemeral simulations normally want a newly-created devdax namespace on
# every boot. Set this to 0 when namespace labels are intentionally persistent.
CXL_NUMA_FRESH_NAMESPACE=${CXL_NUMA_FRESH_NAMESPACE:-1}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

ensure_network() {
    local guest_hostname guest_number address

    guest_hostname=$(hostname)
    case "$guest_hostname" in
        node0)
            guest_number=10
            ;;
        node[1-9]|node[1-9][0-9]*)
            guest_number=${guest_hostname#node}
            guest_number=$((guest_number + 10))
            ;;
        *)
            log "ERROR: cannot derive the static guest address from hostname '$guest_hostname'"
            return 1
            ;;
    esac

    if [ "$guest_number" -gt 254 ]; then
        log "ERROR: derived invalid address suffix $guest_number from '$guest_hostname'"
        return 1
    fi

    address="192.168.100.${guest_number}/24"
    ip link set enp0s2 up
    ip address replace "$address" dev enp0s2
    ip route replace default via 192.168.100.1
    log "Network configured for $guest_hostname at $address"
}

region_size() {
    local capacity_mb

    capacity_mb=$(grep -o 'cxl_region_mb=[0-9][0-9]*' "$CMDLINE_FILE" 2>/dev/null | tail -n1 | cut -d= -f2 || true)
    if [ -z "$capacity_mb" ] || [ "$capacity_mb" -eq 0 ]; then
        capacity_mb=256
        # region_size is used through command substitution, so keep diagnostic
        # output off stdout and reserve stdout for the value alone.
        log "WARNING: cxl_region_mb is absent or invalid; using ${capacity_mb}M" >&2
    fi
    printf '%sM\n' "$capacity_mb"
}

wait_for_memdev() {
    local retry

    for ((retry = 1; retry <= MAX_RETRIES; retry++)); do
        if cxl list -M 2>/dev/null | grep -q '"memdev":"mem0"'; then
            log "CXL memdev mem0 detected"
            return 0
        fi
        log "Waiting for mem0 (attempt $retry/$MAX_RETRIES)"
        sleep "$RETRY_DELAY"
    done

    log "ERROR: mem0 did not appear"
    return 1
}

ensure_region() {
    local size=$1

    if cxl list -R 2>/dev/null | grep -q '"region":"region0"'; then
        log "CXL region0 already exists"
        return 0
    fi

    log "Creating region0 with size $size"
    cxl create-region -m -d decoder0.0 -w 1 mem0 -s "$size" 2>&1 | tee -a "$LOG_FILE"
    udevadm settle
}

namespace_field() {
    local json=$1
    local field=$2
    sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" <<<"$json" | head -n1
}

ensure_devdax_namespace() {
    local namespace_json namespace mode

    namespace_json=$(ndctl list -N 2>/dev/null || true)
    namespace=$(namespace_field "$namespace_json" dev)
    mode=$(namespace_field "$namespace_json" mode)

    if [ -z "$namespace" ]; then
        log "No namespace exists; creating a devdax namespace on region0"
        ndctl create-namespace --mode=devdax --region=region0 2>&1 | tee -a "$LOG_FILE"
    elif [ "$namespace" != namespace0.0 ]; then
        log "ERROR: unexpected namespace '$namespace'; expected namespace0.0"
        return 1
    else
        case "$mode" in
            raw)
                log "Reconfiguring namespace0.0 from raw to devdax"
                ndctl create-namespace --force --reconfig=namespace0.0 --mode=devdax 2>&1 | tee -a "$LOG_FILE"
                ;;
            devdax)
                log "namespace0.0 is already devdax"
                ;;
            *)
                log "ERROR: unsupported namespace mode '$mode' for namespace0.0"
                return 1
                ;;
        esac
    fi

    udevadm settle
}

reset_namespace_for_fresh_boot() {
    local namespace_json namespace

    namespace_json=$(ndctl list -N 2>/dev/null || true)
    namespace=$(namespace_field "$namespace_json" dev)
    if [ -z "$namespace" ]; then
        log "No namespace to discard; creating a fresh devdax namespace"
        return 0
    fi
    if [ "$namespace" != namespace0.0 ]; then
        log "ERROR: unexpected namespace '$namespace'; cannot reset it safely"
        return 1
    fi

    log "Discarding namespace0.0 for fresh devdax provisioning"
    ndctl destroy-namespace --force namespace0.0 2>&1 | tee -a "$LOG_FILE"
    udevadm settle
}

find_dax_device() {
    local dax_path

    dax_path=$(find "$SYS_DAX_DEVICES" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort | head -n1)
    if [ -z "$dax_path" ]; then
        log "ERROR: no DAX device appeared under $SYS_DAX_DEVICES"
        return 1
    fi
    printf '%s\n' "$dax_path"
}

dax_mode() {
    local dax_device=$1
    local dax_json

    dax_json=$(daxctl list -d "$dax_device")
    namespace_field "$dax_json" mode
}

ensure_system_ram() {
    local dax_device=$1
    local mode

    mode=$(dax_mode "$dax_device")
    case "$mode" in
        system-ram)
            log "$dax_device is already online as system RAM"
            ;;
        devdax)
            log "Reconfiguring $dax_device as system RAM; this may take several minutes"
            daxctl reconfigure-device --mode=system-ram "$dax_device" 2>&1 | tee -a "$LOG_FILE"
            udevadm settle
            ;;
        *)
            log "ERROR: unsupported DAX mode '$mode' for $dax_device"
            return 1
            ;;
    esac
}

wait_for_usable_devdax() {
    local dax_device=$1
    local device_path="$DEV_ROOT/$dax_device"
    local retry

    for ((retry = 1; retry <= MAX_RETRIES; retry++)); do
        if [ "${CXL_NUMA_TEST_MODE:-0}" = 1 ]; then
            [ -e "$device_path" ] && return 0
        elif [ -c "$device_path" ] && bash -c 'exec 3<>"$1"' bash "$device_path" 2>/dev/null; then
            return 0
        fi
        log "Waiting for usable $device_path (attempt $retry/$MAX_RETRIES)"
        sleep "$RETRY_DELAY"
    done

    log "ERROR: $device_path exists but is not usable"
    return 1
}

ensure_devdax() {
    local dax_device=$1
    local mode

    mode=$(dax_mode "$dax_device")
    case "$mode" in
        devdax)
            ;;
        system-ram)
            log "Reconfiguring $dax_device from system RAM to devdax"
            daxctl reconfigure-device --mode=devdax "$dax_device" 2>&1 | tee -a "$LOG_FILE"
            udevadm settle
            ;;
        *)
            log "ERROR: unsupported DAX mode '$mode' for $dax_device"
            return 1
            ;;
    esac
}

verify_final_state() {
    local dax_device=$1
    local device_path="$DEV_ROOT/$dax_device"
    local mode numa_output

    if [ "${CXL_NUMA_TEST_MODE:-0}" = 1 ]; then
        [ -e "$device_path" ] || {
            log "ERROR: $device_path does not exist"
            return 1
        }
    elif [ ! -c "$device_path" ]; then
        log "ERROR: $device_path is not a character device"
        return 1
    fi

    mode=$(dax_mode "$dax_device")
    if [ "$CXL_NUMA_MODE" = system-ram ]; then
        if [ "$mode" != system-ram ]; then
            log "ERROR: $dax_device mode is '$mode', expected system-ram"
            return 1
        fi

        numa_output=$(numactl -H)
        printf '%s\n' "$numa_output" | tee -a "$LOG_FILE"
        if ! grep -Eq '^node 1 size: [1-9][0-9]* MB' <<<"$numa_output"; then
            log "ERROR: NUMA node 1 is absent or has no online memory"
            return 1
        fi
        log "Verified $dax_device as system RAM on NUMA node 1"
    else
        if [ "$mode" != devdax ]; then
            log "ERROR: $dax_device mode is '$mode', expected devdax"
            return 1
        fi
        wait_for_usable_devdax "$dax_device"
        log "Verified $device_path is usable devdax"
    fi
}

main() {
    local size dax_device

    log "Starting post-boot CXL NUMA setup"
    ensure_network

    for module in cxl_core cxl_pci cxl_acpi cxl_port cxl_mem dax device_dax kmem; do
        modprobe "$module" 2>/dev/null || true
    done

    size=$(region_size)
    wait_for_memdev
    ensure_region "$size"
    if [ "$CXL_NUMA_FRESH_NAMESPACE" = 1 ]; then
        reset_namespace_for_fresh_boot
    fi
    ensure_devdax_namespace
    dax_device=$(find_dax_device)
    case "$CXL_NUMA_MODE" in
        devdax)
            ensure_devdax "$dax_device"
            log "Keeping $dax_device in devdax mode"
            ;;
        system-ram)
            ensure_system_ram "$dax_device"
            ;;
        *)
            log "ERROR: unsupported CXL_NUMA_MODE '$CXL_NUMA_MODE' (expected devdax or system-ram)"
            return 1
            ;;
    esac
    verify_final_state "$dax_device"
    log "Post-boot CXL NUMA setup completed"
}

main "$@"
