#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORKFLOW="$ROOT_DIR/.github/workflows/ci.yml"

if ! grep -q "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')" "$WORKFLOW"; then
    echo "vm-assets job must be restricted to push tag refs only" >&2
    exit 1
fi

if ! grep -q "LEGOMEM_ASSET_DRY_RUN=1 script/build_legomem_vm_assets.sh" "$WORKFLOW"; then
    echo "normal CI must keep a dry-run VM asset script smoke test" >&2
    exit 1
fi

if ! grep -q "dist/qemu.img" "$WORKFLOW" || ! grep -q "dist/bzImage" "$WORKFLOW"; then
    echo "tagged vm-assets job must still publish qemu.img and bzImage" >&2
    exit 1
fi
