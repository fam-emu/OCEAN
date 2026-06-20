#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <legomem_server> <legomem_qemu_bench>" >&2
    exit 2
fi

SERVER_BIN=$1
BENCH_BIN=$2
PORT=${LEGOMEM_TEST_PORT:-21999}
LOG=$(mktemp)

cleanup() {
    if [ -n "${SERVER_PID:-}" ]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
        wait "$SERVER_PID" >/dev/null 2>&1 || true
    fi
    rm -f "$LOG"
}
trap cleanup EXIT

"$SERVER_BIN" "$PORT" >"$LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 50); do
    if grep -q "LegoMem server listening" "$LOG"; then
        break
    fi
    sleep 0.1
done

if ! grep -q "LegoMem server listening" "$LOG"; then
    echo "server did not start" >&2
    cat "$LOG" >&2
    exit 1
fi

OUTPUT=$("$BENCH_BIN" 127.0.0.1 "$PORT" 1 8 4096)
printf '%s\n' "$OUTPUT"

if ! grep -q "logical_ops=16 protocol_ops=16 " <<<"$OUTPUT"; then
    echo "4 KiB operations should use one protocol request per logical operation" >&2
    exit 1
fi
