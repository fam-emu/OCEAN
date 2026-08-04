import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "script"))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ocean-mpl")

from figures_6_9.schemas import write_rows


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def normalized_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "normalized"
    source = "synthetic"

    fig6 = []
    for repetition in range(2):
        for coverage in (0, 25, 50, 70, 80, 90, 100):
            for node_id in (0, 1):
                fig6.append(
                    {
                        "coverage_pct": coverage,
                        "node_id": node_id,
                        "throughput_txn_s": 6000 + coverage * 600 + node_id * 500 + repetition * 50,
                        "repetition": repetition,
                        "source": source,
                    }
                )
    write_rows(root / "fig6.csv", "fig6", fig6)

    fig7 = []
    for repetition in range(2):
        for protocol, offset, slope in (
            ("Tigon", 8000, 25),
            ("DS2PL+", 4000, 35),
            ("Sundial+", 2000, 45),
        ):
            for ratio in range(0, 101, 10):
                fig7.append(
                    {
                        "protocol": protocol,
                        "write_ratio_pct": ratio,
                        "throughput_txn_s": 6000 + offset - ratio * slope + repetition * 10,
                        "repetition": repetition,
                        "source": source,
                    }
                )
    write_rows(root / "fig7.csv", "fig7", fig7)

    policies = (
        "Baseline", "Interleave", "NUMA", "Frequency", "PageTableAware",
        "FIFO", "HeatAware", "Hybrid", "Locality", "CacheFrequency",
        "HugePage", "Lifetime", "LoadBalance",
    )
    fig8 = []
    for repetition in range(2):
        for backend, backend_offset in (("SHM", 0.0), ("TCP", 0.12)):
            for policy_index, policy in enumerate(policies):
                fig8.append(
                    {
                        "backend": backend,
                        "policy": policy,
                        "elapsed_s": 1.0 + backend_offset + policy_index * 0.005 + repetition * 0.001,
                        "repetition": repetition,
                        "source": source,
                    }
                )
    write_rows(root / "fig8.csv", "fig8", fig8)

    samples = []
    for operation, base in (("os", 18), ("cas_raw", 120), ("cas_flush", 180), ("or", 438), ("full_rt", 1200)):
        for sample_id in range(100):
            samples.append(
                {
                    "operation": operation,
                    "sample_id": sample_id,
                    "latency_ns": base + sample_id * 0.5,
                    "source": source,
                }
            )
    write_rows(root / "fig9_samples.csv", "fig9_samples", samples)

    params = []
    for scenario, os_ns, latency, or_ns, gap in (
        ("Default", 20, 150, 20, 4.0),
        ("Real HW", 18, 144, 438, 4.6),
        ("OCEAN calibrated", 18, 144, 438, 4.6),
    ):
        params.append(
            {
                "scenario": scenario,
                "o_s_ns": os_ns,
                "L_ns": latency,
                "o_r_ns": or_ns,
                "g_ns": gap,
                "bandwidth_gbps": 64.0 / gap,
                "source": source,
            }
        )
    write_rows(root / "fig9_params.csv", "fig9_params", params)

    contention = []
    for step in range(101):
        utilization = step / 100
        contention.extend(
            [
                {
                    "series": "OCEAN default",
                    "lock_count": 0,
                    "effective_utilization": utilization,
                    "added_latency_ns": max(0.0, (utilization - 0.5) * 200),
                    "repetition": 0,
                    "source": source,
                },
                {
                    "series": "OCEAN calibrated",
                    "lock_count": 0,
                    "effective_utilization": utilization,
                    "added_latency_ns": max(0.0, (utilization - 0.2) * 1400),
                    "repetition": 0,
                    "source": source,
                },
            ]
        )
    for lock_count, utilization, latency in ((8, 0.125, 0), (4, 0.25, 70), (2, 0.5, 420), (1, 1.0, 1120)):
        contention.append(
            {
                "series": "Measured (real HW)",
                "lock_count": lock_count,
                "effective_utilization": utilization,
                "added_latency_ns": latency,
                "repetition": 0,
                "source": source,
            }
        )
    write_rows(root / "fig9_contention.csv", "fig9_contention", contention)
    return root
