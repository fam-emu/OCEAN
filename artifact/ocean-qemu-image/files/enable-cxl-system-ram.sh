#!/bin/bash

# Explicit opt-in helper. The normal boot service leaves the device in devdax
# mode; use this only when Linux NUMA system RAM is desired.
set -euo pipefail

export CXL_NUMA_MODE=system-ram
exec "$(dirname "${BASH_SOURCE[0]}")/fixed-numa-setup.sh" "$@"
