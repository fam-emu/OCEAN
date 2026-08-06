#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
build_dir=${1:-"$repo_root/build-llm-transfer"}
cxx=${CXX:-c++}

mkdir -p "$build_dir"

"$cxx" \
  -std=c++20 \
  -O2 \
  -Wall \
  -Wextra \
  -Werror \
  -I"$repo_root/include" \
  "$repo_root/src/region_decoder.cpp" \
  "$repo_root/src/llm_bulk_core.cpp" \
  "$repo_root/src/llm_transfer_model.cpp" \
  "$repo_root/src/llm_transfer_replay.cpp" \
  -o "$build_dir/llm_transfer_replay"

echo "$build_dir/llm_transfer_replay"
