import subprocess
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
from figures_6_9.runners.fig6_tigon import (
    build_detached_command,
    build_guest_prepare_command,
    build_tpcc_argv,
    hcc_budget_bytes,
    parse_average_commit,
    tigon_launch_order,
)
from figures_6_9.runners.fig7_tigon import (
    build_ycsb_argv,
    protocol_settings,
)


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


def test_planner_expands_repository_and_workdir_placeholders(tmp_path: Path):
    tigon = tmp_path / "tigon"
    tigon.mkdir()
    config = {
        "run": {"repetitions": 1},
        "fig6": {
            "workdir": str(tigon),
            "coverage_pct": [25],
            "command": [
                "python3",
                "{repo_root}/script/figures_6_9/runners/fig6_tigon.py",
                "--tigon-root",
                "{workdir}",
                "--coverage",
                "{coverage_pct}",
            ],
        },
    }

    plans = plan_tpcc_commands(config, tmp_path, tmp_path / "run")

    assert plans[0].argv == (
        "python3",
        str(tmp_path / "script/figures_6_9/runners/fig6_tigon.py"),
        "--tigon-root",
        str(tigon),
        "--coverage",
        "25",
    )


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        (0, 1_048_576),
        (25, 53_215_232),
        (70, 147_115_212),
        (100, 209_715_200),
    ],
)
def test_hcc_coverage_maps_to_200_mib_budget(coverage: int, expected: int):
    assert hcc_budget_bytes(coverage) == expected


def test_parse_average_commit_uses_completed_summary():
    output = (
        "commit: 100 abort: 2\n"
        "average commit: 6123.5 abort: 10.0\n"
    )

    assert parse_average_commit(output) == 6123.5


def test_parse_average_commit_rejects_missing_summary():
    with pytest.raises(ValidationError, match="average commit"):
        parse_average_commit("commit: 100 abort: 2\n")


def test_figure6_command_matches_original_mixed_tpcc_sweep():
    argv = build_tpcc_argv(
        node_id=1,
        servers="192.168.100.10:1234;192.168.100.11:1234",
        budget_bytes=53_215_232,
        dax_path="/dev/dax0.0",
        workers=1,
        run_seconds=10,
        warmup_seconds=2,
    )

    assert argv[0] == "./bench_tpcc"
    assert "--id=1" in argv
    assert "--query=mixed" in argv
    assert "--hw_cc_budget=53215232" in argv
    assert "--enable_scc=1" in argv
    assert "--scc_mechanism=WriteThrough" in argv
    assert "--pre_migrate=None" in argv
    assert "--persist_latency=0" in argv
    assert "--wal_group_commit_time=0" in argv
    assert "--wal_group_commit_size=0" in argv


def test_figure6_command_can_override_query_for_diagnostics():
    argv = build_tpcc_argv(
        node_id=0,
        servers="192.168.100.10:1234;192.168.100.11:1234",
        budget_bytes=0,
        dax_path="/dev/dax0.0",
        workers=1,
        run_seconds=10,
        warmup_seconds=2,
        query="neworder",
    )

    assert "--query=neworder" in argv


def test_detached_launch_uses_setsid_instead_of_waited_shell_background_job():
    command = build_detached_command(
        "/root/pasha",
        "/root/pasha/fig6.log",
        ["./bench_tpcc", "--id=0"],
    )

    assert "setsid -f" in command
    assert "nohup" not in command
    assert not command.rstrip().endswith("&")


def test_guest_prepare_fails_closed_when_dax_is_not_a_character_device(
    tmp_path: Path,
):
    valid = build_guest_prepare_command(
        "/dev/null", str(tmp_path), "codex-no-proc", str(tmp_path / "logs")
    )
    invalid = build_guest_prepare_command(
        str(tmp_path / "missing-dax"),
        str(tmp_path),
        "codex-no-proc",
        str(tmp_path / "logs"),
    )

    assert subprocess.run(valid, shell=True, check=False).returncode == 0
    assert subprocess.run(invalid, shell=True, check=False).returncode != 0


def test_tigon_launches_metadata_owner_before_peer():
    assert tigon_launch_order() == (0, 1)


@pytest.mark.parametrize(
    ("paper_name", "binary_protocol"),
    [("Tigon", "TwoPLPasha"), ("DS2PL+", "TwoPL"), ("Sundial+", "Sundial")],
)
def test_figure7_protocol_mapping(paper_name: str, binary_protocol: str):
    assert protocol_settings(paper_name).binary_protocol == binary_protocol


def test_figure7_converts_write_ratio_to_tigon_read_ratio():
    argv = build_ycsb_argv(
        node_id=0,
        servers="192.168.100.10:1234;192.168.100.11:1234",
        protocol="DS2PL+",
        write_ratio_pct=70,
        dax_path="/dev/dax0.0",
        workers=1,
        run_seconds=10,
        warmup_seconds=2,
        entry_num=512,
    )

    assert argv[0] == "./bench_ycsb"
    assert "--protocol=TwoPL" in argv
    assert "--query=rmw" in argv
    assert "--read_write_ratio=30" in argv
    assert "--cross_ratio=100" in argv
    assert "--use_cxl_transport=1" in argv
    assert "--cxl_trans_entry_struct_size=65536" in argv
    assert "--cxl_trans_entry_num=512" in argv
    assert not any(item.startswith("--enable_scc=") for item in argv)


def test_figure7_tigon_enables_hardware_and_software_coherence_paths():
    argv = build_ycsb_argv(
        node_id=1,
        servers="192.168.100.10:1234;192.168.100.11:1234",
        protocol="Tigon",
        write_ratio_pct=40,
        dax_path="/dev/dax0.0",
        workers=1,
        run_seconds=10,
        warmup_seconds=2,
    )

    assert "--protocol=TwoPLPasha" in argv
    assert "--cxl_trans_entry_struct_size=2048" in argv
    assert "--hw_cc_budget=209715200" in argv
    assert "--enable_scc=1" in argv
    assert "--scc_mechanism=WriteThrough" in argv
    assert "--pre_migrate=NonPart" in argv


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
