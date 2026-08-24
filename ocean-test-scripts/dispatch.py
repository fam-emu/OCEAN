"""Dispatch helpers shared by the public workload commands."""

from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys
from collections.abc import Sequence


class UsageError(ValueError):
    """A command-line selection error."""


def split_legacy_flag(argv: Sequence[str]) -> tuple[bool, list[str]]:
    values = list(argv)
    positions = [index for index, value in enumerate(values) if value == "--legacy"]
    if len(positions) > 1:
        raise UsageError("--legacy may be specified only once")
    if not positions:
        return False, values
    values.pop(positions[0])
    return True, values


def run_selected(legacy: bool, legacy_script: pathlib.Path, reconstructed_module: str, argv: Sequence[str]) -> int:
    if legacy:
        result = subprocess.run([sys.executable, str(legacy_script), *argv], check=False)
        return result.returncode
    module = importlib.import_module(reconstructed_module)
    return int(module.main(list(argv)))
