#!/bin/bash

PORT=${1:-9999}
# Resolve the topology relative to this script so the server can be launched
# from build/ (where the binary lives) while the topology lives in
# qemu_integration/.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TOPOLOGY=${2:-$SCRIPT_DIR/topology_simple.txt}
LSA_FILE=${CXL_LSA_FILE:-${3:-/dev/shm/lsa1.raw}}
# CXL data capacity in MB. MUST match CXL_CAPACITY_MB in launch_qemu_cxl*.sh:
# the server's /dev/shm/cxlmemsim_shared backing and QEMU's cxl-mem1
# memory-backend-file are the same file, so a size mismatch corrupts the
# mapping (and previously made the guest region alias/trap with #UD).
CAPACITY_MB=${CXL_CAPACITY_MB:-${4:-2048}}

echo "Starting CXLMemSim server on port $PORT with topology $TOPOLOGY (capacity ${CAPACITY_MB}MB, LSA backed by $LSA_FILE)"
./cxlmemsim_server --port="$PORT" --topology="$TOPOLOGY" --capacity="$CAPACITY_MB" --lsa-backing-file="$LSA_FILE"
