set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cxlmemsim_build_dir="${SCRIPT_DIR}/../submodules/CXLMemSim/build"

# Start the primary server
set -x
"${cxlmemsim_build_dir}/cxlmemsim_server" --comm-mode distributed --transport-mode tcp --node-id 0 --capacity 1024 --port 9999
set +x

