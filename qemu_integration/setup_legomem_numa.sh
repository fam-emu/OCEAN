#!/bin/bash

set -euo pipefail

LOG_FILE="/var/log/legomem_numa_setup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

main() {
    log "Starting LegoMem NUMA verification"

    if command -v numactl >/dev/null 2>&1; then
        log "NUMA hardware view:"
        numactl --hardware 2>&1 | tee -a "$LOG_FILE"
    else
        log "numactl is not installed; checking sysfs nodes"
        ls -d /sys/devices/system/node/node* 2>&1 | tee -a "$LOG_FILE"
    fi

    log "LegoMem NUMA verification completed"
}

main "$@"
