from __future__ import annotations

from .common import candidate_path, invoke_file


def main(argv=None):
    return invoke_file("candidate_boot_to_dax", candidate_path("boot_dax_smoke.py"), list(argv or []))
