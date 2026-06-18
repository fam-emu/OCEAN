#!/bin/bash

set -euo pipefail

CLIENTS=${1:-4}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TEST_BIN=${TEST_BIN:-"$SCRIPT_DIR/test_qemu_legomem_api"}

if [ ! -x "$TEST_BIN" ] && [ -x "$SCRIPT_DIR/build/test_qemu_legomem_api" ]; then
    TEST_BIN="$SCRIPT_DIR/build/test_qemu_legomem_api"
fi

for i in $(seq 1 "$CLIENTS"); do
    "$TEST_BIN" > "legomem_client_${i}.log" 2>&1 &
done

wait
echo "Completed $CLIENTS QEMU LegoMem API smoke clients"
