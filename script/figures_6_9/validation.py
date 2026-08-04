from collections import defaultdict
from itertools import product

from .errors import ValidationError


FIG6_COVERAGE = {0, 25, 50, 70, 80, 90, 100}
FIG7_RATIOS = set(range(0, 101, 10))
FIG7_PROTOCOLS = {"Tigon", "DS2PL+", "Sundial+"}
FIG8_BACKENDS = {"SHM", "TCP"}
FIG8_POLICIES = {
    "Baseline",
    "Interleave",
    "NUMA",
    "Frequency",
    "PageTableAware",
    "FIFO",
    "HeatAware",
    "Hybrid",
    "Locality",
    "CacheFrequency",
    "HugePage",
    "Lifetime",
    "LoadBalance",
}
FIG9_OPERATIONS = {"os", "cas_raw", "cas_flush", "or", "full_rt"}
FIG9_SCENARIOS = {"Default", "Real HW", "OCEAN calibrated"}


def _reject_duplicates(
    table: str, rows: list[dict[str, object]], keys: tuple[str, ...]
) -> None:
    seen = set()
    for row in rows:
        primary_key = tuple(row[key] for key in keys)
        if primary_key in seen:
            raise ValidationError(
                f"{table}: duplicate primary key {primary_key}"
            )
        seen.add(primary_key)


def _validate_sweep(
    table: str,
    rows: list[dict[str, object]],
    dimensions: tuple[str, ...],
    expected: set[tuple[object, ...]],
) -> None:
    by_repetition: dict[int, set[tuple[object, ...]]] = defaultdict(set)
    for row in rows:
        by_repetition[int(row["repetition"])].add(
            tuple(row[name] for name in dimensions)
        )
    for repetition, observed in by_repetition.items():
        missing = expected - observed
        unexpected = observed - expected
        if missing:
            raise ValidationError(
                f"{table} repetition {repetition}: missing sweep keys: {sorted(missing)}"
            )
        if unexpected:
            raise ValidationError(
                f"{table} repetition {repetition}: unexpected sweep keys: {sorted(unexpected)}"
            )


def _validate_fig6(rows: list[dict[str, object]]) -> None:
    _reject_duplicates("fig6", rows, ("coverage_pct", "node_id", "repetition"))
    _validate_sweep(
        "fig6",
        rows,
        ("coverage_pct", "node_id"),
        set(product(FIG6_COVERAGE, {0, 1})),
    )


def _validate_fig7(rows: list[dict[str, object]]) -> None:
    _reject_duplicates(
        "fig7", rows, ("protocol", "write_ratio_pct", "repetition")
    )
    _validate_sweep(
        "fig7",
        rows,
        ("protocol", "write_ratio_pct"),
        set(product(FIG7_PROTOCOLS, FIG7_RATIOS)),
    )


def _validate_fig8(rows: list[dict[str, object]]) -> None:
    _reject_duplicates("fig8", rows, ("backend", "policy", "repetition"))
    _validate_sweep(
        "fig8",
        rows,
        ("backend", "policy"),
        set(product(FIG8_BACKENDS, FIG8_POLICIES)),
    )


def _validate_fig9_samples(rows: list[dict[str, object]]) -> None:
    _reject_duplicates("fig9_samples", rows, ("operation", "sample_id"))
    operations = {row["operation"] for row in rows}
    missing = FIG9_OPERATIONS - operations
    if missing:
        raise ValidationError(
            f"fig9_samples: missing operations: {sorted(missing)}"
        )


def _validate_fig9_params(rows: list[dict[str, object]]) -> None:
    _reject_duplicates("fig9_params", rows, ("scenario",))
    scenarios = {row["scenario"] for row in rows}
    if scenarios != FIG9_SCENARIOS:
        raise ValidationError(
            f"fig9_params: expected scenarios {sorted(FIG9_SCENARIOS)}, got {sorted(scenarios)}"
        )


def _validate_fig9_contention(rows: list[dict[str, object]]) -> None:
    _reject_duplicates(
        "fig9_contention",
        rows,
        ("series", "effective_utilization", "repetition"),
    )
    measured = {
        int(row["lock_count"])
        for row in rows
        if row["series"] == "Measured (real HW)"
    }
    if measured and measured != {1, 2, 4, 8}:
        raise ValidationError(
            "fig9_contention: measured series requires lock counts 1, 2, 4, and 8"
        )


VALIDATORS = {
    "fig6": _validate_fig6,
    "fig7": _validate_fig7,
    "fig8": _validate_fig8,
    "fig9_samples": _validate_fig9_samples,
    "fig9_params": _validate_fig9_params,
    "fig9_contention": _validate_fig9_contention,
}


def validate_rows(
    table: str, rows: list[dict[str, object]], allow_mixed: bool = False
) -> None:
    if table not in VALIDATORS:
        raise ValidationError(f"unknown table: {table}")
    if not rows:
        raise ValidationError(f"{table}: no rows")
    sources = {str(row["source"]) for row in rows}
    if not allow_mixed and len(sources) != 1:
        raise ValidationError(
            f"{table}: mixed source types: {sorted(sources)}"
        )
    VALIDATORS[table](rows)
