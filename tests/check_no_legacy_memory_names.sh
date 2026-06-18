#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

old_lc=$(printf '%s%s%s' c x l)
old_uc=$(printf '%s%s%s' C X L)
old_project="${old_uc}MemSim"
old_project_lc="${old_lc}memsim"

if find src include -iname "*${old_lc}*" -print -quit | grep -q .; then
    find src include -iname "*${old_lc}*" -print
    exit 1
fi

if grep -RInE "${old_lc}|${old_uc}|${old_project}|${old_project_lc}" src include; then
    exit 1
fi
