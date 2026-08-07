import os
from pathlib import Path
import subprocess

import pytest


POLICY_TUPLES = {
    "Baseline": "none,none,none,none",
    "Interleave": "interleave,none,none,none",
    "NUMA": "numa,none,none,none",
    "Frequency": "none,frequency,none,none",
    "PageTableAware": "none,none,pagetableaware,none",
    "FIFO": "none,none,none,fifo",
    "HeatAware": "none,heataware,none,none",
    "Hybrid": "none,hybrid,none,none",
    "Locality": "none,locality,none,none",
    "CacheFrequency": "none,none,none,frequency",
    "HugePage": "none,none,hugepage,none",
    "Lifetime": "none,lifetime,none,none",
    "LoadBalance": "none,loadbalance,none,none",
}


def _write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def runner(repo_root: Path) -> Path:
    return repo_root / "workloads/gromacs/run_figure8.sh"


@pytest.fixture
def fake_environment(tmp_path: Path) -> dict[str, str]:
    launcher_body = """#!/usr/bin/env bash
set -u
printf 'LAUNCHER=%s\\n' "$0"
for arg in "$@"; do
    printf 'ARG=%s\\n' "$arg"
done
exit "${FAKE_LAUNCHER_RC:-0}"
"""
    tool_body = "#!/usr/bin/env bash\nexit 0\n"
    shm_launcher = _write_executable(tmp_path / "shm-launcher", launcher_body)
    tcp_launcher = _write_executable(tmp_path / "tcp-launcher", launcher_body)
    cxlmemsim = _write_executable(tmp_path / "cxlmemsim_legacy", tool_body)
    gmx_mpi = _write_executable(tmp_path / "gmx_mpi", tool_body)
    tpr = tmp_path / "benchMEM.tpr"
    tpr.write_bytes(b"test tpr")
    return {
        "FIG8_SHM_LAUNCHER": str(shm_launcher),
        "FIG8_TCP_LAUNCHER": str(tcp_launcher),
        "FIG8_CXLMEMSIM": str(cxlmemsim),
        "FIG8_GMX_MPI": str(gmx_mpi),
        "FIG8_TPR": str(tpr),
        "FIG8_STEPS": "10000",
        "FIG8_NTOMP": "1",
        "FIG8_CPUSET": "0",
        "FIG8_PEBS_PERIOD": "1000",
    }


def _run(
    runner: Path,
    fake_environment: dict[str, str],
    *args: str,
    env_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ | fake_environment | (env_updates or {})
    return subprocess.run(
        [str(runner), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


@pytest.mark.parametrize(("label", "policy_tuple"), POLICY_TUPLES.items())
def test_maps_all_paper_policy_labels(
    runner: Path,
    fake_environment: dict[str, str],
    label: str,
    policy_tuple: str,
):
    result = _run(
        runner,
        fake_environment,
        "--backend",
        "SHM",
        "--policy",
        label,
    )

    assert result.returncode == 0, result.stderr
    assert "FIG8_BACKEND=SHM" in result.stdout
    assert f"FIG8_POLICY={label}" in result.stdout
    assert f"FIG8_POLICY_TUPLE={policy_tuple}" in result.stdout
    assert f"ARG=-k\nARG={policy_tuple}\n" in result.stdout
    assert result.stdout.rstrip().endswith("Finished mdrun")


@pytest.mark.parametrize(
    ("backend", "expected_launcher"),
    (("SHM", "shm-launcher"), ("TCP", "tcp-launcher")),
)
def test_selects_exact_backend_launcher(
    runner: Path,
    fake_environment: dict[str, str],
    backend: str,
    expected_launcher: str,
):
    result = _run(
        runner,
        fake_environment,
        "--backend",
        backend,
        "--policy",
        "Baseline",
    )

    assert result.returncode == 0, result.stderr
    assert f"LAUNCHER={Path(fake_environment[f'FIG8_{backend}_LAUNCHER'])}" in result.stdout
    other_launcher = "tcp-launcher" if expected_launcher == "shm-launcher" else "shm-launcher"
    assert expected_launcher in result.stdout
    assert other_launcher not in result.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("--backend", "RDMA", "--policy", "Baseline"),
        ("--backend", "SHM", "--policy", "UnknownPolicy"),
        ("--backend", "SHM"),
        ("--policy", "Baseline"),
        ("--bogus", "value"),
    ),
)
def test_rejects_invalid_arguments(
    runner: Path,
    fake_environment: dict[str, str],
    args: tuple[str, ...],
):
    result = _run(runner, fake_environment, *args)

    assert result.returncode != 0
    assert "ERROR:" in result.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("FIG8_STEPS", "0"),
        ("FIG8_NTOMP", "-1"),
        ("FIG8_PEBS_PERIOD", "abc"),
    ),
)
def test_rejects_non_positive_numeric_settings(
    runner: Path,
    fake_environment: dict[str, str],
    name: str,
    value: str,
):
    result = _run(
        runner,
        fake_environment,
        "--backend",
        "SHM",
        "--policy",
        "Baseline",
        env_updates={name: value},
    )

    assert result.returncode != 0
    assert name in result.stderr


def test_requires_selected_backend_launcher(
    runner: Path,
    fake_environment: dict[str, str],
):
    env = fake_environment | {"FIG8_TCP_LAUNCHER": "/missing/tcp-launcher"}
    result = _run(
        runner,
        env,
        "--backend",
        "TCP",
        "--policy",
        "Baseline",
    )

    assert result.returncode != 0
    assert "FIG8_TCP_LAUNCHER" in result.stderr


def test_propagates_backend_launcher_failure(
    runner: Path,
    fake_environment: dict[str, str],
):
    result = _run(
        runner,
        fake_environment,
        "--backend",
        "SHM",
        "--policy",
        "Baseline",
        env_updates={"FAKE_LAUNCHER_RC": "17"},
    )

    assert result.returncode == 17
    assert "Finished mdrun" not in result.stdout
