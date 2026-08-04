from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

from .errors import ReproductionError


@dataclass(frozen=True)
class RunResult:
    argv: tuple[str, ...]
    returncode: int
    output: str
    started_utc: str
    ended_utc: str
    dry_run: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_command(
    argv: list[str],
    log_path: Path,
    env: dict[str, str],
    timeout_s: float,
    dry_run: bool,
    cwd: Path | None = None,
) -> RunResult:
    if not argv:
        raise ReproductionError("refusing to execute an empty command")
    started = _utc_now()
    if dry_run:
        return RunResult(tuple(argv), 0, "", started, started, True)

    try:
        process = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ | env,
            timeout=timeout_s,
            check=False,
            shell=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
        raise ReproductionError(
            f"command {argv[0]!r} timed out after {timeout_s} seconds"
        ) from error
    except OSError as error:
        raise ReproductionError(f"cannot execute {argv[0]!r}: {error}") from error

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(process.stdout, encoding="utf-8")
    return RunResult(
        tuple(argv),
        process.returncode,
        process.stdout,
        started,
        _utc_now(),
        False,
    )
