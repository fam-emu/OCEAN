#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_QEMU="$SCRIPT_DIR/../library/qemu/build/qemu-system-x86_64"

if [ -x "$DEFAULT_QEMU" ]; then
    QEMU_BINARY=${QEMU_BINARY:-"$DEFAULT_QEMU"}
else
    QEMU_BINARY=${QEMU_BINARY:-/usr/local/bin/qemu-system-x86_64}
fi

KERNEL_IMAGE=${KERNEL_IMAGE:-./bzImage}
DISK_IMAGE=${DISK_IMAGE:-./qemu.img}
VM_BASE_MEMORY=${VM_BASE_MEMORY:-4G}
LEGOMEM_NODE_SIZE=${LEGOMEM_NODE_SIZE:-1G}
VM_TOTAL_MEMORY=${VM_TOTAL_MEMORY:-5G}
VM_MAX_MEMORY=${VM_MAX_MEMORY:-16G}
export LEGOMEM_SERVER_HOST=${LEGOMEM_SERVER_HOST:-127.0.0.1}
export LEGOMEM_SERVER_PORT=${LEGOMEM_SERVER_PORT:-9999}
export LEGOMEM_REGION_ID=${LEGOMEM_REGION_ID:-1}

echo "Starting QEMU with LegoMem NUMA node"
echo "  QEMU binary: ${QEMU_BINARY}"
echo "  LegoMem server: ${LEGOMEM_SERVER_HOST}:${LEGOMEM_SERVER_PORT}"
echo "  LegoMem region: ${LEGOMEM_REGION_ID}"
echo "  NUMA size: ${LEGOMEM_NODE_SIZE}"

exec "$QEMU_BINARY" \
    --enable-kvm \
    -cpu qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt \
    -smp 4 \
    -machine q35 \
    -m "$VM_TOTAL_MEMORY",slots=8,maxmem="$VM_MAX_MEMORY" \
    -object memory-backend-ram,id=ram-node0,size="$VM_BASE_MEMORY" \
    -numa node,nodeid=0,cpus=0-3,memdev=ram-node0 \
    -object memory-backend-legomem,id=legomem-node1,size="$LEGOMEM_NODE_SIZE",server="$LEGOMEM_SERVER_HOST",port="$LEGOMEM_SERVER_PORT",region-id="$LEGOMEM_REGION_ID" \
    -numa node,nodeid=1,memdev=legomem-node1 \
    -kernel "$KERNEL_IMAGE" \
    -append "root=/dev/sda rw console=ttyS0,115200 nokaslr" \
    -drive file="$DISK_IMAGE",index=0,media=disk,format=raw \
    -netdev tap,id=net0,ifname=tap0,script=no,downscript=no \
    -device virtio-net-pci,netdev=net0,mac=52:54:00:00:00:01 \
    -fsdev local,security_model=none,id=fsdev0,path=/dev/shm \
    -device virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshm,bus=pcie.0 \
    -nographic \
    "$@"
