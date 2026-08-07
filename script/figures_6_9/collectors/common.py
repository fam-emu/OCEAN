from dataclasses import dataclass
from pathlib import Path
import re

from ..config import expand_command
from ..errors import ConfigError, UnavailableError, ValidationError
from ..execution import RunResult, run_command


@dataclass(frozen=True)
class PlannedRun:
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path
    values: dict[str, object]


def resolve_workdir(
    section_name: str, section: dict[str, object], repo_root: Path
) -> Path:
    raw = Path(str(section.get("workdir", ".")))
    workdir = raw if raw.is_absolute() else repo_root / raw
    if not workdir.is_dir():
        raise UnavailableError(
            f"{section_name}.workdir does not exist: {workdir}"
        )
    return workdir


def compile_pattern(
    section_name: str,
    section: dict[str, object],
    default: re.Pattern[str],
) -> re.Pattern[str]:
    configured = section.get("result_regex")
    if configured is None:
        return default
    try:
        return re.compile(str(configured), re.MULTILINE)
    except re.error as error:
        raise ConfigError(f"{section_name}.result_regex is invalid: {error}") from error


def make_plan(
    section_name: str,
    section: dict[str, object],
    repo_root: Path,
    log_path: Path,
    values: dict[str, object],
) -> PlannedRun:
    workdir = resolve_workdir(section_name, section, repo_root)
    command = section.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(token, str) for token in command
    ):
        raise ConfigError(f"{section_name}.command must be a nonempty TOML array")
    expansion_values = {
        "repo_root": str(repo_root),
        "workdir": str(workdir),
        **values,
    }
    argv = tuple(expand_command(command, expansion_values))
    return PlannedRun(argv, workdir, log_path, values)


def execute_plan(
    plan: PlannedRun,
    timeout_s: float,
    dry_run: bool,
    env: dict[str, str] | None = None,
) -> RunResult:
    result = run_command(
        list(plan.argv),
        plan.log_path,
        env or {},
        timeout_s,
        dry_run,
        plan.cwd,
    )
    if not result.dry_run and result.returncode != 0:
        raise ValidationError(
            f"command failed with exit {result.returncode}; raw log: {plan.log_path}"
        )
    return result
