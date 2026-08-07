import json
from pathlib import Path

import pytest

from figures_6_9.collectors.fig9_logp import (
    calibrated_curve,
    default_contention_ns,
    derive_logp,
    parse_jsonl,
    plan_logp_command,
)
from figures_6_9.collectors import fig9_logp
from figures_6_9.errors import ConfigError, ValidationError
from figures_6_9.execution import RunResult


def test_derive_logp_matches_paper_equation():
    params = derive_logp(os_ns=18.0, or_ns=438.0, rtt_ns=1200.0, g_ns=4.6)

    assert params["L_ns"] == 144.0
    assert params["bandwidth_gbps"] == pytest.approx(64.0 / 4.6)


def test_derive_logp_rejects_impossible_rtt():
    with pytest.raises(ValidationError, match="invalid measured LogP"):
        derive_logp(os_ns=20.0, or_ns=438.0, rtt_ns=100.0, g_ns=4.6)


def test_parse_jsonl_splits_records():
    text = "\n".join(
        [
            json.dumps(
                {
                    "type": "metadata",
                    "rank": 0,
                    "world_size": 2,
                    "version": 1,
                }
            ),
            json.dumps(
                {
                    "type": "sample",
                    "operation": "os",
                    "sample_id": 0,
                    "latency_ns": 18.0,
                }
            ),
            json.dumps(
                {
                    "type": "summary",
                    "name": "rtt_ns",
                    "value": 1200.0,
                }
            ),
            json.dumps(
                {
                    "type": "contention",
                    "lock_count": 1,
                    "effective_utilization": 1.0,
                    "added_latency_ns": 1400.0,
                }
            ),
        ]
    )

    parsed = parse_jsonl(text, source="measured", repetition=0)

    assert parsed.samples[0]["operation"] == "os"
    assert parsed.contention[0]["lock_count"] == 1
    assert parsed.summaries["rtt_ns"] == 1200.0
    assert parsed.metadata[0]["world_size"] == 2


def test_parse_jsonl_reports_invalid_line_number():
    with pytest.raises(ValidationError, match="line 2"):
        parse_jsonl('{"type":"metadata","rank":0}\nnot-json\n', "measured", 0)


def test_contention_models_match_current_ocean_rule_and_measured_knots():
    assert default_contention_ns(0.4) == 0.0
    assert default_contention_ns(0.8) == pytest.approx(20.0)
    assert default_contention_ns(1.0) == pytest.approx(100.0)

    curve = calibrated_curve([(0.5, 300.0), (1.0, 1400.0)])
    assert curve[0] == (0.0, 0.0)
    assert curve[50] == (0.5, 300.0)
    assert curve[-1] == (1.0, 1400.0)


def test_plan_refuses_unacknowledged_dax_writes(tmp_path: Path):
    config = {
        "run": {"repetitions": 1},
        "fig9": {
            "workdir": str(tmp_path),
            "command": ["runner", "--dax", "{dax_path}"],
            "dax_path": "/dev/dax0.0",
            "iterations": 10,
            "map_offset": 0,
            "map_size": 4096,
            "acknowledge_dax_writes": False,
        },
    }

    with pytest.raises(ConfigError, match="acknowledge_dax_writes"):
        plan_logp_command(config, tmp_path, tmp_path / "run")


@pytest.mark.parametrize(
    ("map_offset", "map_size"),
    [(4096, 4096), (0, 2 * 1024 * 1024 + 1)],
)
def test_plan_refuses_dax_ranges_outside_approved_first_2_mib(
    tmp_path: Path, map_offset: int, map_size: int
):
    config = {
        "run": {"repetitions": 1},
        "fig9": {
            "workdir": str(tmp_path),
            "command": ["runner", "--dax", "{dax_path}"],
            "dax_path": "/dev/dax0.0",
            "iterations": 10,
            "map_offset": map_offset,
            "map_size": map_size,
            "acknowledge_dax_writes": True,
        },
    }

    with pytest.raises(ConfigError, match="approved first 2 MiB"):
        plan_logp_command(config, tmp_path, tmp_path / "run")


def test_collect_requires_configured_sample_count(monkeypatch, tmp_path: Path):
    records = [
        {
            "type": "metadata",
            "rank": rank,
            "world_size": 2,
            "hostname": f"vm{rank}",
            "iterations": 2,
        }
        for rank in (0, 1)
    ]
    records.extend(
        {
            "type": "sample",
            "operation": operation,
            "sample_id": 0,
            "latency_ns": latency,
        }
        for operation, latency in (
            ("os", 18), ("cas_raw", 120), ("cas_flush", 180),
            ("or", 438), ("full_rt", 1200),
        )
    )
    records.extend(
        {"type": "summary", "name": name, "value": value}
        for name, value in (("os_ns", 18), ("or_ns", 438), ("rtt_ns", 1200), ("g_ns", 4.6))
    )
    records.extend(
        {
            "type": "contention",
            "lock_count": count,
            "effective_utilization": 1 / count,
            "added_latency_ns": 100 / count,
        }
        for count in (1, 2, 4, 8)
    )
    output = "\n".join(json.dumps(record) for record in records)
    result = RunResult(("runner",), 0, output, "start", "end", False)
    monkeypatch.setattr(fig9_logp, "execute_plan", lambda *args: result)
    config = {
        "run": {"source": "measured", "timeout_s": 1},
        "fig9": {
            "workdir": str(tmp_path),
            "command": ["runner"],
            "dax_path": "/dev/dax0.0",
            "iterations": 2,
            "map_offset": 0,
            "map_size": 2 * 1024 * 1024,
            "acknowledge_dax_writes": True,
            "default_o_s_ns": 20,
            "default_L_ns": 150,
            "default_o_r_ns": 20,
            "default_g_ns": 4,
        },
    }

    with pytest.raises(ValidationError, match="expected 2 samples"):
        fig9_logp.collect_logp(config, tmp_path, tmp_path / "run", False)
