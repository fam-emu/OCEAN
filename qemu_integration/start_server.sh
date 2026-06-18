#!/bin/bash

set -euo pipefail

PORT=${1:-9999}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SERVER_BIN=${LEGOMEM_SERVER_BIN:-"$SCRIPT_DIR/legomem_server"}

if [ ! -x "$SERVER_BIN" ] && [ -x "$SCRIPT_DIR/build/legomem_server" ]; then
    SERVER_BIN="$SCRIPT_DIR/build/legomem_server"
fi

echo "Starting LegoMem server on port $PORT"
exec "$SERVER_BIN" "$PORT"
