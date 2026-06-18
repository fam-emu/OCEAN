#!/usr/bin/env bash
set -euo pipefail

OUT_DIR=${1:-dist}
IMAGE_SIZE=${LEGOMEM_IMAGE_SIZE:-2G}
ROOTFS_RELEASE=${LEGOMEM_ROOTFS_RELEASE:-noble}
ROOTFS_MIRROR=${LEGOMEM_ROOTFS_MIRROR:-http://archive.ubuntu.com/ubuntu}

mkdir -p "$OUT_DIR"
OUT_DIR=$(cd "$OUT_DIR" && pwd)

if [ "${LEGOMEM_ASSET_DRY_RUN:-0}" = "1" ]; then
    printf 'LegoMem CI dry-run image\n' > "$OUT_DIR/qemu.img"
    printf 'LegoMem CI dry-run kernel\n' > "$OUT_DIR/bzImage"
    (cd "$OUT_DIR" && sha256sum qemu.img bzImage > SHA256SUMS)
    exit 0
fi

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "missing required command: $1" >&2
        exit 1
    fi
}

need_cmd debootstrap
need_cmd mkfs.ext4
need_cmd sha256sum
need_cmd sudo

KERNEL_IMAGE=$(ls -1 /boot/vmlinuz-* 2>/dev/null | sort -V | tail -1 || true)
if [ -z "$KERNEL_IMAGE" ]; then
    echo "no /boot/vmlinuz-* found; install a linux-image package first" >&2
    exit 1
fi

WORK_DIR=$(mktemp -d)
MOUNT_DIR="$WORK_DIR/rootfs"
ROOTFS_DIR="$WORK_DIR/bootstrap"
mkdir -p "$MOUNT_DIR" "$ROOTFS_DIR"

cleanup() {
    if mountpoint -q "$MOUNT_DIR"; then
        sudo umount "$MOUNT_DIR"
    fi
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

IMAGE_PATH="$OUT_DIR/qemu.img"
truncate -s "$IMAGE_SIZE" "$IMAGE_PATH"
mkfs.ext4 -F -L LEGOMEM "$IMAGE_PATH"

sudo mount -o loop "$IMAGE_PATH" "$MOUNT_DIR"
sudo debootstrap \
    --variant=minbase \
    --include=systemd-sysv,openssh-server,iproute2,ca-certificates,netbase \
    "$ROOTFS_RELEASE" \
    "$MOUNT_DIR" \
    "$ROOTFS_MIRROR"

echo "legomem" | sudo tee "$MOUNT_DIR/etc/hostname" >/dev/null
sudo tee "$MOUNT_DIR/etc/fstab" >/dev/null <<'FSTAB'
LABEL=LEGOMEM / ext4 defaults 0 1
FSTAB

sudo mkdir -p "$MOUNT_DIR/etc/legomem"
sudo tee "$MOUNT_DIR/etc/legomem/build-info" >/dev/null <<EOF
name=LegoMem CI image
release=$ROOTFS_RELEASE
created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

sudo umount "$MOUNT_DIR"
cp "$KERNEL_IMAGE" "$OUT_DIR/bzImage"

(cd "$OUT_DIR" && sha256sum qemu.img bzImage > SHA256SUMS)
