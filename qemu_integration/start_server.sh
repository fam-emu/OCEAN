#!/bin/bash

set -euo pipefail

PORT=${1:-9999}
SERVER_BIN=${LEGOMEM_SERVER_BIN:-./legomem_server}

if [ ! -x "$SERVER_BIN" ] && [ -x "./build/legomem_server" ]; then
    SERVER_BIN=./build/legomem_server
fi

echo "Starting LegoMem server on port $PORT"
exec "$SERVER_BIN" "$PORT"
