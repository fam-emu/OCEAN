from __future__ import annotations

from .common import candidate_path, invoke_file


def main(argv=None):
    return invoke_file("candidate_osu_allgather", candidate_path("run_osu_allgather.py"), list(argv or []))
