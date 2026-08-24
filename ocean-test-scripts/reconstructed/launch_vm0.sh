#!/bin/bash
set -euo pipefail
export CXL_HOST_ID=0 CXL_TAP=tap0 CXL_MAC=52:54:00:00:00:10 CXL_CANDIDATE_IMAGE="$CXL_CANDIDATE_IMAGE_VM0" CXL_CANDIDATE_IMAGE_FORMAT=qcow2
exec "$(dirname "${BASH_SOURCE[0]}")/launch_vm.sh" "$@"
