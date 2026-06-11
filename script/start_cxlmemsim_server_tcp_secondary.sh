set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cxlmemsim_build_dir="${SCRIPT_DIR}/../submodules/CXLMemSim/build"

# Start the secondary server.
# Primary server's IP is 172.16.205.32, which might need to change accordingly.
set -x
"${cxlmemsim_build_dir}/cxlmemsim_server" --comm-mode distributed --transport-mode tcp --node-id 1 --capacity 1024 --tcp-peers 0:172.16.205.32:9999 --tcp-port 5555  --port 9999
set +x

