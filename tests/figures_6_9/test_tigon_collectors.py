import sys
from pathlib import Path

import pytest

from figures_6_9.collectors.fig6_tpcc import (
    collect_tpcc,
    parse_tpcc,
    plan_tpcc_commands,
)
from figures_6_9.collectors.fig7_ycsb import parse_ycsb, plan_ycsb_commands
from figures_6_9.errors import UnavailableError, ValidationError


def test_parse_tpcc_emits_node_rows():
    text = (
        "node=0 NewOrder throughput=6123 txn/s\n"
        "node=1 NewOrder throughput=6177 txn/s\n"
    )

    rows = parse_tpcc(text, 0, 2, "measured")

    assert rows[0] == {
        "coverage_pct": 0,
        "node_id": 0,
        "throughput_txn_s": 6123.0,
        "repetition": 2,
        "source": "measured",
    }
    assert len(rows) == 2


def test_parse_ycsb_rejects_latency_only_output():
    with pytest.raises(ValidationError, match="throughput record"):
        parse_ycsb("AverageLatency(us)=12.0", "Tigon", 0, 0, "measured")


def test_planners_expand_complete_configured_sweeps(tmp_path: Path):
    config = {
        "run": {"repetitions": 2},
        "fig6": {
            "workdir": str(tmp_path),
            "coverage_pct": [0, 25],
            "command": ["runner", "--coverage", "{coverage_pct}"],
        },
        "fig7": {
            "workdir": str(tmp_path),
            "protocols": ["Tigon", "DS2PL+"],
            "write_ratio_pct": [0, 10],
            "command": ["runner", "{protocol}", "{write_ratio_pct}"],
        },
    }

    fig6 = plan_tpcc_commands(config, tmp_path, tmp_path / "run")
    fig7 = plan_ycsb_commands(config, tmp_path, tmp_path / "run")

    assert len(fig6) == 4
    assert fig6[-1].argv == ("runner", "--coverage", "25")
    assert len(fig7) == 8
    assert fig7[-1].argv == ("runner", "DS2PL+", "10")


def test_planner_reports_missing_tigon_workdir(tmp_path: Path):
    config = {
        "run": {"repetitions": 1},
        "fig6": {
            "workdir": "workloads/tigon",
            "coverage_pct": [0],
            "command": ["runner"],
        },
    }

    with pytest.raises(UnavailableError, match="fig6.workdir"):
        plan_tpcc_commands(config, tmp_path, tmp_path / "run")


def test_collect_tpcc_executes_runner_and_preserves_raw_log(tmp_path: Path):
    runner = tmp_path / "runner.py"
    runner.write_text(
        "print('node=0 NewOrder throughput=6123 txn/s')\n"
        "print('node=1 NewOrder throughput=6177 txn/s')\n",
        encoding="utf-8",
    )
    config = {
        "run": {
            "repetitions": 1,
            "timeout_s": 5,
            "source": "measured",
        },
        "fig6": {
            "workdir": str(tmp_path),
            "coverage_pct": [0],
            "command": [sys.executable, str(runner), "{coverage_pct}"],
        },
    }

    rows, runs = collect_tpcc(config, tmp_path, tmp_path / "evidence", False)

    assert [row["throughput_txn_s"] for row in rows] == [6123.0, 6177.0]
    assert len(runs) == 1
    assert (tmp_path / "evidence/raw/fig6/coverage-000-rep-000.log").is_file()
