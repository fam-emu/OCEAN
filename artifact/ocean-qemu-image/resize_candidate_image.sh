#!/usr/bin/env bash
# Expand only the candidate Packer image.  Never point this helper at build/.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
image="$script_dir/disk-image/qemu.img"

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <new-size>" >&2
    echo "Example: $0 72G" >&2
    exit 2
fi

case "$1" in
    *[!0-9GM] | '')
        echo "new-size must be an integer followed by G or M (for example, 72G)" >&2
        exit 2
        ;;
esac

if [ ! -f "$image" ]; then
    echo "candidate image is missing: $image" >&2
    exit 1
fi

qemu-img resize "$image" "$1"
echo "Expanded $image to $1."
echo "Boot the candidate and run 'resize2fs /dev/sda1' inside the guest to grow its ext4 filesystem."
