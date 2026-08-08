from pathlib import Path
import re

from ..errors import ValidationError
from ..execution import RunResult
from .common import PlannedRun, execute_plan, make_plan


FATAL_MARKERS = (
    "Fatal error:",
    "terminate called",
    "TIMEOUT:",
    "GROMACS finished with exit code: 1",
)
WALL_TIME = re.compile(
    r"Wall time:\s*(?P<seconds>[0-9.]+)\s*s", re.IGNORECASE
)
TIME_TABLE = re.compile(
    r"^\s*Time:\s+[0-9.]+\s+(?P<seconds>[0-9.]+)(?:\s+[0-9.]+)?\s*$",
    re.MULTILINE,
)
COMPLETION_MARKERS = ("Finished mdrun", "Performance:")


def parse_gromacs(
    text: str,
    backend: str,
    policy: str,
    repetition: int,
    source: str,
) -> dict[str, object]:
    if any(marker in text for marker in FATAL_MARKERS):
        raise ValidationError("fig8: fatal marker in GROMACS output")
    if "Number of Threads created: 0" in text:
        raise ValidationError("fig8: zero-work simulator run")
    if not any(marker in text for marker in COMPLETION_MARKERS):
        raise ValidationError("fig8: missing GROMACS completion marker")
    match = WALL_TIME.search(text) or TIME_TABLE.search(text)
    if not match:
        raise ValidationError("fig8: missing positive wall time")
    elapsed_s = float(match["seconds"])
    if elapsed_s <= 0:
        raise ValidationError("fig8: missing positive wall time")
    return {
        "backend": backend,
        "policy": policy,
        "elapsed_s": elapsed_s,
        "repetition": repetition,
        "source": source,
    }


def plan_gromacs_commands(
    config: dict, repo_root: Path, run_root: Path
) -> list[PlannedRun]:
    section = config["fig8"]
    repetitions = int(config["run"].get("repetitions", 3))
    plans = []
    for repetition in range(repetitions):
        for backend in section["backends"]:
            for policy in section["policies"]:
                values = {
                    "backend": str(backend),
                    "policy": str(policy),
                    "repetition": repetition,
                }
                slug = re.sub(r"[^a-z0-9]+", "-", str(policy).lower()).strip("-")
                log_path = (
                    run_root
                    / "raw/fig8"
                    / f"{str(backend).lower()}-{slug}-rep-{repetition:03d}.log"
                )
                plans.append(
                    make_plan("fig8", section, repo_root, log_path, values)
                )
    return plans


def collect_gromacs(
    config: dict, repo_root: Path, run_root: Path, dry_run: bool
) -> tuple[list[dict[str, object]], list[RunResult]]:
    source = str(config["run"].get("source", "measured"))
    timeout_s = float(config["run"].get("timeout_s", 3600))
    rows: list[dict[str, object]] = []
    runs = []
    for plan in plan_gromacs_commands(config, repo_root, run_root):
        result = execute_plan(plan, timeout_s, dry_run)
        runs.append(result)
        if not result.dry_run:
            rows.append(
                parse_gromacs(
                    result.output,
                    str(plan.values["backend"]),
                    str(plan.values["policy"]),
                    int(plan.values["repetition"]),
                    source,
                )
            )
    return rows, runs
