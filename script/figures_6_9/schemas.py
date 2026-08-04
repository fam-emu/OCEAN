import csv
import math
from pathlib import Path
from typing import Callable

from .errors import ValidationError


Caster = Callable[[object], object]

SCHEMAS: dict[str, dict[str, Caster]] = {
    "fig6": {
        "coverage_pct": int,
        "node_id": int,
        "throughput_txn_s": float,
        "repetition": int,
        "source": str,
    },
    "fig7": {
        "protocol": str,
        "write_ratio_pct": int,
        "throughput_txn_s": float,
        "repetition": int,
        "source": str,
    },
    "fig8": {
        "backend": str,
        "policy": str,
        "elapsed_s": float,
        "repetition": int,
        "source": str,
    },
    "fig9_samples": {
        "operation": str,
        "sample_id": int,
        "latency_ns": float,
        "source": str,
    },
    "fig9_params": {
        "scenario": str,
        "o_s_ns": float,
        "L_ns": float,
        "o_r_ns": float,
        "g_ns": float,
        "bandwidth_gbps": float,
        "source": str,
    },
    "fig9_contention": {
        "series": str,
        "lock_count": int,
        "effective_utilization": float,
        "added_latency_ns": float,
        "repetition": int,
        "source": str,
    },
}

POSITIVE_COLUMNS = {
    "fig6": ("throughput_txn_s",),
    "fig7": ("throughput_txn_s",),
    "fig8": ("elapsed_s",),
    "fig9_samples": ("latency_ns",),
    "fig9_params": (
        "o_s_ns",
        "L_ns",
        "o_r_ns",
        "g_ns",
        "bandwidth_gbps",
    ),
}

ALLOWED_SOURCES = {"measured", "paper_reference", "synthetic"}


def coerce_rows(
    table: str, rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    if table not in SCHEMAS:
        raise ValidationError(f"unknown table: {table}")
    spec = SCHEMAS[table]
    converted = []
    for row_number, row in enumerate(rows, start=1):
        missing = set(spec) - row.keys()
        extra = row.keys() - set(spec)
        if missing:
            raise ValidationError(
                f"{table} row {row_number}: missing columns: {sorted(missing)}"
            )
        if extra:
            raise ValidationError(
                f"{table} row {row_number}: unexpected columns: {sorted(extra)}"
            )
        try:
            item = {name: caster(row[name]) for name, caster in spec.items()}
        except (TypeError, ValueError) as error:
            raise ValidationError(
                f"{table} row {row_number}: cannot coerce value: {error}"
            ) from error
        if item["source"] not in ALLOWED_SOURCES:
            raise ValidationError(
                f"{table} row {row_number}: invalid source {item['source']!r}"
            )
        for key in POSITIVE_COLUMNS.get(table, ()):
            value = float(item[key])
            if not math.isfinite(value) or value <= 0:
                raise ValidationError(
                    f"{table}.{key} must be finite and positive"
                )
        if table == "fig9_contention":
            utilization = float(item["effective_utilization"])
            latency = float(item["added_latency_ns"])
            if not math.isfinite(utilization) or not 0 <= utilization <= 1:
                raise ValidationError(
                    "fig9_contention.effective_utilization must be between 0 and 1"
                )
            if not math.isfinite(latency) or latency < 0:
                raise ValidationError(
                    "fig9_contention.added_latency_ns must be finite and nonnegative"
                )
        converted.append(item)
    return converted


def write_rows(
    path: Path, table: str, rows: list[dict[str, object]]
) -> None:
    typed_rows = coerce_rows(table, rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SCHEMAS[table]))
        writer.writeheader()
        writer.writerows(typed_rows)


def read_rows(path: Path, table: str) -> list[dict[str, object]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return coerce_rows(table, list(csv.DictReader(handle)))
    except OSError as error:
        raise ValidationError(f"cannot read {table} table {path}: {error}") from error
