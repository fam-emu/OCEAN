#!/bin/bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
qemu=${QEMU_BINARY:-$root/lib/qemu/build/qemu-system-x86_64}
data=${QEMU_DATA_DIR:-$root/lib/qemu/pc-bios}
image=${CXL_CANDIDATE_IMAGE:-$root/artifact/ocean-qemu-image/disk-image/qemu.img}
image_format=${CXL_CANDIDATE_IMAGE_FORMAT:-raw}
kernel=$root/artifact/ocean-qemu-image/disk-image/bzImage
host_id=${CXL_HOST_ID:-0}
tap=${CXL_TAP:-tap${host_id}}
mac=${CXL_MAC:-52:54:00:00:00:1${host_id}}
capacity=${CXL_CAPACITY_MB:-1024}
lsa=${CXL_LSA_FILE:-/dev/shm/lsa1.raw}
backing=${CXL_BACKING_FILE:-/dev/shm/cxlmemsim_shared}

case "${1:---kvm}" in
  --kvm|--kvm-direct) accel=(--enable-kvm -cpu qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt);;
  --tcg) accel=(--accel tcg,thread=multi -cpu max);;
  *) echo "Usage: $0 [--kvm|--kvm-direct|--tcg]" >&2; exit 2;;
esac

export CXL_TRANSPORT_MODE=${CXL_TRANSPORT_MODE:-tcp}
export CXL_MEMSIM_HOST=${CXL_MEMSIM_HOST:-127.0.0.1}
export CXL_MEMSIM_PORT=${CXL_MEMSIM_PORT:-9999}
export CXL_HOST_ID=$host_id
export CXL_EXECUTION_MODE=${CXL_EXECUTION_MODE:-memsim}
exec "$qemu" "${accel[@]}" -L "$data" -m 16G,maxmem=32G,slots=8 -smp 2 -M q35,cxl=on \
  -kernel "$kernel" -append "root=/dev/sda1 rw console=ttyS0,115200 nokaslr cxl_region_mb=${capacity}" \
  -drive "file=${image},index=0,media=disk,format=${image_format}" \
  -netdev "tap,id=net0,ifname=${tap},script=no,downscript=no" -device "virtio-net-pci,netdev=net0,mac=${mac}" \
  -fsdev local,security_model=none,id=fsdev0,path=/dev/shm -device virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshm,bus=pcie.0 \
  -device pxb-cxl,bus_nr=12,bus=pcie.0,id=cxl.1 -device cxl-rp,port=0,bus=cxl.1,id=root_port13,chassis=0,slot=0 \
  -device cxl-rp,port=1,bus=cxl.1,id=root_port14,chassis=0,slot=1 -device cxl-type3,bus=root_port13,persistent-memdev=cxl-mem1,lsa=cxl-lsa1,id=cxl-pmem0,sn=0x1 \
  -device cxl-type1,bus=root_port14,size=1G,cache-size=64M -device virtio-cxl-accel-pci,bus=pcie.0 \
  -object "memory-backend-file,id=cxl-mem1,share=on,mem-path=${backing},size=${capacity}M" \
  -object "memory-backend-file,id=cxl-lsa1,share=on,mem-path=${lsa},size=256K" -M cxl-fmw.0.targets.0=cxl.1,cxl-fmw.0.size=4G -nographic
