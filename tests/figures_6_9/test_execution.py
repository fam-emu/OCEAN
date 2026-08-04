import sys
from pathlib import Path

import pytest

from figures_6_9.errors import ReproductionError
from figures_6_9.execution import run_command


def test_run_command_captures_combined_output(tmp_path: Path):
    result = run_command(
        [sys.executable, "-c", "import sys; print('out', flush=True); print('err', file=sys.stderr)"],
        tmp_path / "raw.log",
        {},
        5,
        False,
    )

    assert result.returncode == 0
    assert result.output == "out\nerr\n"
    assert (tmp_path / "raw.log").read_text() == result.output


def test_dry_run_does_not_execute(tmp_path: Path):
    marker = tmp_path / "marker"

    result = run_command(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        tmp_path / "raw.log",
        {},
        5,
        True,
    )

    assert result.dry_run is True
    assert not marker.exists()
    assert not (tmp_path / "raw.log").exists()


def test_timeout_is_reported_and_logged(tmp_path: Path):
    log = tmp_path / "timeout.log"

    with pytest.raises(ReproductionError, match="timed out after 0.1 seconds"):
        run_command(
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(1)"],
            log,
            {},
            0.1,
            False,
        )

    assert "started" in log.read_text()
