from pathlib import Path
import re

from ..errors import ValidationError
from ..execution import RunResult
from .common import PlannedRun, compile_pattern, execute_plan, make_plan


YCSB_PATTERN = re.compile(
    r"(?:YCSB\s+)?throughput=(?P<throughput>[0-9.]+)\s+txn/s",
    re.MULTILINE,
)


def parse_ycsb(
    text: str,
    protocol: str,
    write_ratio_pct: int,
    repetition: int,
    source: str,
    pattern: re.Pattern[str] = YCSB_PATTERN,
) -> dict[str, object]:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValidationError(
            f"fig7: expected one throughput record, got {len(matches)}"
        )
    return {
        "protocol": protocol,
        "write_ratio_pct": write_ratio_pct,
        "throughput_txn_s": float(matches[0]["throughput"]),
        "repetition": repetition,
        "source": source,
    }


def plan_ycsb_commands(
    config: dict, repo_root: Path, run_root: Path
) -> list[PlannedRun]:
    section = config["fig7"]
    repetitions = int(config["run"].get("repetitions", 3))
    plans = []
    for repetition in range(repetitions):
        for protocol in section["protocols"]:
            for ratio in section["write_ratio_pct"]:
                values = {
                    "protocol": str(protocol),
                    "write_ratio_pct": int(ratio),
                    "repetition": repetition,
                }
                safe_protocol = str(protocol).lower().replace("+", "plus")
                log_path = (
                    run_root
                    / "raw/fig7"
                    / f"{safe_protocol}-write-{int(ratio):03d}-rep-{repetition:03d}.log"
                )
                plans.append(
                    make_plan("fig7", section, repo_root, log_path, values)
                )
    return plans


def collect_ycsb(
    config: dict, repo_root: Path, run_root: Path, dry_run: bool
) -> tuple[list[dict[str, object]], list[RunResult]]:
    section = config["fig7"]
    pattern = compile_pattern("fig7", section, YCSB_PATTERN)
    source = str(config["run"].get("source", "measured"))
    timeout_s = float(config["run"].get("timeout_s", 3600))
    rows: list[dict[str, object]] = []
    runs = []
    for plan in plan_ycsb_commands(config, repo_root, run_root):
        result = execute_plan(plan, timeout_s, dry_run)
        runs.append(result)
        if not result.dry_run:
            rows.append(
                parse_ycsb(
                    result.output,
                    str(plan.values["protocol"]),
                    int(plan.values["write_ratio_pct"]),
                    int(plan.values["repetition"]),
                    source,
                    pattern,
                )
            )
    return rows, runs
