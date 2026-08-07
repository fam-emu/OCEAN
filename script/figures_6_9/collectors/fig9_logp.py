from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np

from ..errors import ConfigError, ValidationError
from ..execution import RunResult
from .common import PlannedRun, execute_plan, make_plan


APPROVED_DAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ParsedLogP:
    samples: list[dict[str, object]]
    contention: list[dict[str, object]]
    summaries: dict[str, float]
    metadata: list[dict[str, object]]


def derive_logp(
    os_ns: float, or_ns: float, rtt_ns: float, g_ns: float
) -> dict[str, float]:
    latency = (rtt_ns - 2.0 * os_ns - 2.0 * or_ns) / 2.0
    if not all(math.isfinite(value) for value in (os_ns, or_ns, rtt_ns, g_ns)):
        raise ValidationError("fig9: invalid measured LogP components")
    if min(os_ns, or_ns, latency, g_ns) <= 0:
        raise ValidationError("fig9: invalid measured LogP components")
    return {
        "o_s_ns": os_ns,
        "L_ns": latency,
        "o_r_ns": or_ns,
        "g_ns": g_ns,
        "bandwidth_gbps": 64.0 / g_ns,
    }


def parse_jsonl(text: str, source: str, repetition: int) -> ParsedLogP:
    samples = []
    contention = []
    summaries: dict[str, float] = {}
    metadata = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValidationError(
                f"fig9: invalid JSONL at line {line_number}: {error.msg}"
            ) from error
        if not isinstance(record, dict) or "type" not in record:
            raise ValidationError(f"fig9: malformed record at line {line_number}")
        record_type = record["type"]
        try:
            if record_type == "sample":
                latency = float(record["latency_ns"])
                if not math.isfinite(latency) or latency <= 0:
                    raise ValueError("latency_ns must be positive")
                samples.append(
                    {
                        "operation": str(record["operation"]),
                        "sample_id": int(record["sample_id"]),
                        "latency_ns": latency,
                        "source": source,
                    }
                )
            elif record_type == "contention":
                contention.append(
                    {
                        "series": "Measured (real HW)",
                        "lock_count": int(record["lock_count"]),
                        "effective_utilization": float(
                            record["effective_utilization"]
                        ),
                        "added_latency_ns": float(record["added_latency_ns"]),
                        "repetition": repetition,
                        "source": source,
                    }
                )
            elif record_type == "summary":
                summaries[str(record["name"])] = float(record["value"])
            elif record_type == "metadata":
                metadata.append(dict(record))
            else:
                raise ValidationError(
                    f"fig9: unknown record type {record_type!r} at line {line_number}"
                )
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError(
                f"fig9: malformed {record_type!r} record at line {line_number}: {error}"
            ) from error
    return ParsedLogP(samples, contention, summaries, metadata)


def default_contention_ns(utilization: float) -> float:
    if not 0 <= utilization <= 1:
        raise ValidationError("fig9: utilization must be between 0 and 1")
    if utilization < 0.5:
        return 0.0
    if utilization < 0.8:
        return (utilization - 0.5) / 0.3 * 20.0
    return 20.0 + (utilization - 0.8) / 0.2 * 80.0


def calibrated_curve(
    measured: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if not measured:
        raise ValidationError("fig9: no measured contention points")
    points = sorted({(0.0, 0.0), *measured})
    if points[-1][0] != 1.0:
        raise ValidationError("fig9: measured contention must include utilization 1.0")
    x = np.linspace(0.0, 1.0, 101)
    y = np.interp(
        x, [point[0] for point in points], [point[1] for point in points]
    )
    return list(zip(x.tolist(), y.tolist(), strict=True))


def plan_logp_command(
    config: dict, repo_root: Path, run_root: Path
) -> PlannedRun:
    section = config["fig9"]
    if section.get("acknowledge_dax_writes") is not True:
        raise ConfigError(
            "fig9.acknowledge_dax_writes must be true before collection"
        )
    values = {
        "dax_path": str(section["dax_path"]),
        "iterations": int(section["iterations"]),
        "map_offset": int(section["map_offset"]),
        "map_size": int(section["map_size"]),
    }
    if (
        values["map_offset"] != 0
        or values["map_size"] <= 0
        or values["map_size"] > APPROVED_DAX_BYTES
    ):
        raise ConfigError(
            "fig9 DAX writes must stay within the approved first 2 MiB at offset 0"
        )
    return make_plan(
        "fig9",
        section,
        repo_root,
        run_root / "raw/fig9/logp-calibration.log",
        values,
    )


def _parameter_row(
    scenario: str, params: dict[str, float], source: str
) -> dict[str, object]:
    return {"scenario": scenario, **params, "source": source}


def collect_logp(
    config: dict, repo_root: Path, run_root: Path, dry_run: bool
) -> tuple[dict[str, list[dict[str, object]]], list[RunResult]]:
    plan = plan_logp_command(config, repo_root, run_root)
    timeout_s = float(config["run"].get("timeout_s", 3600))
    source = str(config["run"].get("source", "measured"))
    result = execute_plan(plan, timeout_s, dry_run)
    if result.dry_run:
        return {
            "fig9_samples": [],
            "fig9_params": [],
            "fig9_contention": [],
        }, [result]

    parsed = parse_jsonl(result.output, source, 0)
    section = config["fig9"]
    operations = {row["operation"] for row in parsed.samples}
    required_operations = {"os", "cas_raw", "cas_flush", "or", "full_rt"}
    if operations != required_operations:
        raise ValidationError(
            f"fig9: missing operation samples: {sorted(required_operations - operations)}"
        )
    expected_samples = int(section["iterations"])
    sample_counts = Counter(str(row["operation"]) for row in parsed.samples)
    short = {
        operation: sample_counts[operation]
        for operation in required_operations
        if sample_counts[operation] != expected_samples
    }
    if short:
        raise ValidationError(
            f"fig9: expected {expected_samples} samples per operation, got {short}"
        )
    required_summaries = {"os_ns", "or_ns", "rtt_ns", "g_ns"}
    if not required_summaries <= parsed.summaries.keys():
        raise ValidationError(
            f"fig9: missing summaries: {sorted(required_summaries - parsed.summaries.keys())}"
        )
    if {int(item.get("rank", -1)) for item in parsed.metadata} != {0, 1}:
        raise ValidationError("fig9: metadata from both MPI ranks is required")
    if any(
        int(item.get("world_size", -1)) != 2
        or int(item.get("iterations", -1)) != expected_samples
        for item in parsed.metadata
    ):
        raise ValidationError("fig9: inconsistent two-rank benchmark metadata")
    hostnames = {str(item.get("hostname", "")) for item in parsed.metadata}
    if "" in hostnames or len(hostnames) != 2:
        raise ValidationError("fig9: metadata from two distinct hosts is required")
    contention_locks = {int(row["lock_count"]) for row in parsed.contention}
    if contention_locks != {1, 2, 4, 8}:
        raise ValidationError(
            "fig9: contention requires lock counts 1, 2, 4, and 8"
        )

    measured = derive_logp(
        parsed.summaries["os_ns"],
        parsed.summaries["or_ns"],
        parsed.summaries["rtt_ns"],
        parsed.summaries["g_ns"],
    )
    default = {
        "o_s_ns": float(section["default_o_s_ns"]),
        "L_ns": float(section["default_L_ns"]),
        "o_r_ns": float(section["default_o_r_ns"]),
        "g_ns": float(section["default_g_ns"]),
        "bandwidth_gbps": 64.0 / float(section["default_g_ns"]),
    }
    params = [
        _parameter_row("Default", default, source),
        _parameter_row("Real HW", measured, source),
        _parameter_row("OCEAN calibrated", measured, source),
    ]

    measured_points = [
        (
            float(row["effective_utilization"]),
            float(row["added_latency_ns"]),
        )
        for row in parsed.contention
    ]
    curves = []
    for utilization in np.linspace(0.0, 1.0, 101):
        curves.append(
            {
                "series": "OCEAN default",
                "lock_count": 0,
                "effective_utilization": float(utilization),
                "added_latency_ns": default_contention_ns(float(utilization)),
                "repetition": 0,
                "source": source,
            }
        )
    for utilization, latency in calibrated_curve(measured_points):
        curves.append(
            {
                "series": "OCEAN calibrated",
                "lock_count": 0,
                "effective_utilization": utilization,
                "added_latency_ns": latency,
                "repetition": 0,
                "source": source,
            }
        )
    return {
        "fig9_samples": parsed.samples,
        "fig9_params": params,
        "fig9_contention": [*parsed.contention, *curves],
    }, [result]
