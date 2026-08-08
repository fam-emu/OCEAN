from pathlib import Path

import pytest

from figures_6_9.collectors.fig8_gromacs import parse_gromacs
from figures_6_9.errors import ValidationError


def test_parse_completed_gromacs_run():
    text = (
        "starting mdrun 'PEPSIN in water'\n"
        "10000 steps\n"
        "Finished mdrun\n"
        "Wall time: 1.083 s\n"
    )

    row = parse_gromacs(text, "SHM", "Baseline", 0, "measured")

    assert row == {
        "backend": "SHM",
        "policy": "Baseline",
        "elapsed_s": 1.083,
        "repetition": 0,
        "source": "measured",
    }


def test_parse_standard_gromacs_time_table():
    text = "Finished mdrun on rank 0\nTime: 4.200 1.050 400.0\n"

    row = parse_gromacs(text, "TCP", "NUMA", 1, "measured")

    assert row["elapsed_s"] == 1.05


def test_parse_native_gromacs_performance_completion():
    text = (
        "starting mdrun 'PEPSIN in water'\n"
        "Time: 4.200 1.050 400.0\n"
        "Performance: 2.814 8.529\n"
    )

    row = parse_gromacs(text, "SHM", "Baseline", 0, "measured")

    assert row["elapsed_s"] == 1.05


@pytest.mark.parametrize(
    "name",
    [
        "cxlmemsim.txt",
        "cxlmemsim_none_frequency_none_none.txt",
    ],
)
def test_rejects_committed_invalid_gromacs_logs(
    repo_root: Path, name: str
):
    text = (repo_root / "artifact/gromacs/gmx" / name).read_text(
        errors="replace"
    )

    with pytest.raises(ValidationError):
        parse_gromacs(text, "SHM", "Baseline", 0, "measured")
