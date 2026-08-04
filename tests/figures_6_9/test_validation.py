from pathlib import Path

import pytest

from figures_6_9.errors import ValidationError
from figures_6_9.schemas import coerce_rows, read_rows, write_rows
from figures_6_9.validation import validate_rows


def complete_fig6_rows(source: str = "measured"):
    return [
        {
            "coverage_pct": coverage,
            "node_id": node,
            "throughput_txn_s": 10_000.0,
            "repetition": 0,
            "source": source,
        }
        for coverage in [0, 25, 50, 70, 80, 90, 100]
        for node in [0, 1]
    ]


def test_fig6_accepts_every_coverage_and_two_nodes():
    validate_rows("fig6", complete_fig6_rows())


def test_validation_rejects_mixed_source_types():
    rows = complete_fig6_rows()
    rows[-1]["source"] = "synthetic"

    with pytest.raises(ValidationError, match="mixed source types"):
        validate_rows("fig6", rows)


def test_validation_rejects_missing_sweep_point():
    with pytest.raises(ValidationError, match="missing sweep keys"):
        validate_rows("fig6", complete_fig6_rows()[:-1])


def test_validation_rejects_duplicate_primary_key():
    rows = complete_fig6_rows()
    rows.append(rows[0].copy())

    with pytest.raises(ValidationError, match="duplicate primary key"):
        validate_rows("fig6", rows)


def test_coerce_rows_rejects_non_finite_metric():
    with pytest.raises(ValidationError, match="finite and positive"):
        coerce_rows(
            "fig8",
            [
                {
                    "backend": "SHM",
                    "policy": "Baseline",
                    "elapsed_s": "nan",
                    "repetition": "0",
                    "source": "measured",
                }
            ],
        )


def test_csv_round_trip_preserves_types(tmp_path: Path):
    path = tmp_path / "fig6.csv"
    rows = complete_fig6_rows()

    write_rows(path, "fig6", rows)

    assert read_rows(path, "fig6") == rows
