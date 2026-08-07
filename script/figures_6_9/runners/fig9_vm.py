#!/usr/bin/env python3
"""Run the Figure 9 two-rank DAX benchmark inside two CXLMemSim VMs."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from figures_6_9.errors import ReproductionError, ValidationError
from figures_6_9.runners.fig6_tigon import Remote, sync_binary


APPROVED_DAX_BYTES = 2 * 1024 * 1024


def validate_acknowledgement(acknowledged: bool) -> None:
    if not acknowledged:
        raise ValidationError(
            "fig9: --acknowledge-dax-write is required before DAX execution"
        )


def validate_nodes(node0: str, node1: str) -> None:
    if not node0 or not node1 or node0 == node1:
        raise ValidationError("fig9: two distinct VM hosts are required")


def validate_dax_range(map_offset: int, map_size: int) -> None:
    if map_offset != 0 or map_size <= 0 or map_size > APPROVED_DAX_BYTES:
        raise ValidationError(
            "fig9: DAX writes must stay within the approved first 2 MiB at offset 0"
        )


def build_mpirun_argv(
    *,
    node0: str,
    node1: str,
    guest_binary: str,
    dax_path: str,
    iterations: int,
    map_offset: int,
    map_size: int,
) -> list[str]:
    validate_nodes(node0, node1)
    validate_dax_range(map_offset, map_size)
    if iterations <= 0:
        raise ValidationError("fig9: iterations must be positive")
    return [
        "mpirun",
        "--allow-run-as-root",
        "-np",
        "2",
        "--host",
        f"{node0},{node1}",
        "--mca",
        "btl",
        "self,vader,tcp",
        guest_binary,
        "--acknowledge-dax-write",
        "--dax",
        dax_path,
        "--iterations",
        str(iterations),
        "--map-offset",
        str(map_offset),
        f"--map-size={map_size}",
    ]


def run(args: argparse.Namespace) -> None:
    validate_acknowledgement(args.acknowledge_dax_write)
    validate_nodes(args.node0, args.node1)
    local_binary = args.binary.resolve()
    if not local_binary.is_file():
        raise ReproductionError(f"fig9: benchmark binary is unavailable: {local_binary}")
    validate_dax_range(args.map_offset, args.map_size)

    remote = Remote(args.ssh_user, args.ssh_port, args.ssh_timeout)
    nodes = (args.node0, args.node1)
    guest_binary = f"{args.guest_workdir}/{local_binary.name}"
    for host in nodes:
        remote.run(
            host,
            f"command -v mpirun >/dev/null && test -c {shlex.quote(args.dax_path)} "
            f"&& mkdir -p {shlex.quote(args.guest_workdir)}",
        )
        sync_binary(
            remote,
            host,
            local_binary,
            args.guest_workdir,
            local_binary.name,
        )

    nested_ssh = (
        "ssh -o BatchMode=yes -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    )
    remote.run(
        args.node0,
        f"{nested_ssh} root@{shlex.quote(args.node1)} true",
    )
    argv = build_mpirun_argv(
        node0=args.node0,
        node1=args.node1,
        guest_binary=guest_binary,
        dax_path=args.dax_path,
        iterations=args.iterations,
        map_offset=args.map_offset,
        map_size=args.map_size,
    )
    command = f"export OMPI_MCA_plm_rsh_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'; exec {shlex.join(argv)}"
    result = remote.run(args.node0, command)
    print(result.stdout, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--node0", default="192.168.100.10")
    parser.add_argument("--node1", default="192.168.100.11")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--ssh-timeout", type=int, default=900)
    parser.add_argument("--guest-workdir", default="/root/ocean")
    parser.add_argument("--dax-path", default="/dev/dax0.0")
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--map-offset", type=int, default=0)
    parser.add_argument("--map-size", type=int, default=APPROVED_DAX_BYTES)
    parser.add_argument("--acknowledge-dax-write", action="store_true")
    return parser


def main() -> int:
    try:
        run(build_parser().parse_args())
    except (ReproductionError, subprocess.SubprocessError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
