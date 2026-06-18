#!/bin/bash

set -euo pipefail

CLIENTS=${1:-4}
TEST_BIN=${TEST_BIN:-./build/test_qemu_legomem_api}

for i in $(seq 1 "$CLIENTS"); do
    "$TEST_BIN" > "legomem_client_${i}.log" 2>&1 &
done

wait
echo "Completed $CLIENTS QEMU LegoMem API smoke clients"
