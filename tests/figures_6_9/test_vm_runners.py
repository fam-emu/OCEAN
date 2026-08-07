from pathlib import Path

import pytest

from figures_6_9.errors import ValidationError
from figures_6_9.runners.fig9_vm import (
    build_mpirun_argv,
    validate_acknowledgement,
    validate_dax_range,
)


def test_figure9_vm_command_uses_two_ranks_and_exact_dax_range():
    argv = build_mpirun_argv(
        node0="192.168.100.10",
        node1="192.168.100.11",
        guest_binary="/root/ocean/cxl_switch_lock_bench_mpi",
        dax_path="/dev/dax0.0",
        iterations=10_000,
        map_offset=0,
        map_size=2_097_152,
    )

    assert argv[:4] == ["mpirun", "--allow-run-as-root", "-np", "2"]
    assert "--host" in argv
    assert "192.168.100.10,192.168.100.11" in argv
    assert argv[-7:] == [
        "--dax",
        "/dev/dax0.0",
        "--iterations",
        "10000",
        "--map-offset",
        "0",
        "--map-size=2097152",
    ]
    assert "--acknowledge-dax-write" in argv


def test_figure9_vm_rejects_range_larger_than_approved_first_2_mib():
    with pytest.raises(ValidationError, match="first 2 MiB"):
        validate_dax_range(0, 2_097_153)


def test_figure9_vm_rejects_nonzero_offset():
    with pytest.raises(ValidationError, match="first 2 MiB"):
        validate_dax_range(4096, 2_093_056)


def test_figure9_vm_requires_explicit_acknowledgement():
    with pytest.raises(ValidationError, match="acknowledge-dax-write"):
        validate_acknowledgement(False)


def test_figure9_vm_requires_two_distinct_hosts():
    with pytest.raises(ValidationError, match="two distinct VM hosts"):
        build_mpirun_argv(
            node0="192.168.100.10",
            node1="192.168.100.10",
            guest_binary="/root/ocean/cxl_switch_lock_bench_mpi",
            dax_path="/dev/dax0.0",
            iterations=10,
            map_offset=0,
            map_size=2_097_152,
        )
