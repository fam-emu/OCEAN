#!/bin/bash
set -euo pipefail
export CXL_HOST_ID=1 CXL_TAP=tap1 CXL_MAC=52:54:00:00:00:11 CXL_CANDIDATE_IMAGE="$CXL_CANDIDATE_IMAGE_VM1" CXL_CANDIDATE_IMAGE_FORMAT=qcow2
exec "$(dirname "${BASH_SOURCE[0]}")/launch_vm.sh" "$@"
