from pathlib import Path
import re

from ..errors import ValidationError
from ..execution import RunResult
from .common import PlannedRun, compile_pattern, execute_plan, make_plan


TPC_PATTERN = re.compile(
    r"node=(?P<node_id>\d+)\s+NewOrder\s+"
    r"throughput=(?P<throughput>[0-9.]+)\s+txn/s",
    re.MULTILINE,
)


def parse_tpcc(
    text: str,
    coverage_pct: int,
    repetition: int,
    source: str,
    pattern: re.Pattern[str] = TPC_PATTERN,
) -> list[dict[str, object]]:
    rows = [
        {
            "coverage_pct": coverage_pct,
            "node_id": int(match["node_id"]),
            "throughput_txn_s": float(match["throughput"]),
            "repetition": repetition,
            "source": source,
        }
        for match in pattern.finditer(text)
    ]
    if len(rows) != 2:
        raise ValidationError(
            f"fig6: expected two node throughput records, got {len(rows)}"
        )
    if {row["node_id"] for row in rows} != {0, 1}:
        raise ValidationError("fig6: expected throughput for node 0 and node 1")
    return sorted(rows, key=lambda row: int(row["node_id"]))


def plan_tpcc_commands(
    config: dict, repo_root: Path, run_root: Path
) -> list[PlannedRun]:
    section = config["fig6"]
    repetitions = int(config["run"].get("repetitions", 3))
    plans = []
    for repetition in range(repetitions):
        for coverage in section["coverage_pct"]:
            values = {
                "coverage_pct": int(coverage),
                "repetition": repetition,
            }
            log_path = (
                run_root
                / "raw/fig6"
                / f"coverage-{int(coverage):03d}-rep-{repetition:03d}.log"
            )
            plans.append(
                make_plan("fig6", section, repo_root, log_path, values)
            )
    return plans


def collect_tpcc(
    config: dict, repo_root: Path, run_root: Path, dry_run: bool
) -> tuple[list[dict[str, object]], list[RunResult]]:
    section = config["fig6"]
    pattern = compile_pattern("fig6", section, TPC_PATTERN)
    source = str(config["run"].get("source", "measured"))
    timeout_s = float(config["run"].get("timeout_s", 3600))
    rows: list[dict[str, object]] = []
    runs = []
    for plan in plan_tpcc_commands(config, repo_root, run_root):
        result = execute_plan(plan, timeout_s, dry_run)
        runs.append(result)
        if not result.dry_run:
            rows.extend(
                parse_tpcc(
                    result.output,
                    int(plan.values["coverage_pct"]),
                    int(plan.values["repetition"]),
                    source,
                    pattern,
                )
            )
    return rows, runs
