#!/usr/bin/env python3
"""Run one two-node Tigon YCSB point for Figure 7."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from figures_6_9.errors import ReproductionError, ValidationError
from figures_6_9.runners.fig6_tigon import (
    MAX_HCC_BYTES,
    Remote,
    _launch,
    _wait_for_marker,
    build_guest_prepare_command,
    parse_average_commit,
    sync_binary,
    tigon_launch_order,
)


@dataclass(frozen=True)
class ProtocolSettings:
    binary_protocol: str
    transport_entry_size: int
    pasha_flags: tuple[str, ...] = ()


PROTOCOLS = {
    "Tigon": ProtocolSettings(
        "TwoPLPasha",
        2048,
        (
            "--enable_migration_optimization=1",
            "--migration_policy=Clock",
            "--when_to_move_out=OnDemand",
            f"--hw_cc_budget={MAX_HCC_BYTES}",
            "--enable_scc=1",
            "--scc_mechanism=WriteThrough",
            "--pre_migrate=NonPart",
            "--model_cxl_search_overhead=0",
        ),
    ),
    "DS2PL+": ProtocolSettings("TwoPL", 65536),
    "Sundial+": ProtocolSettings("Sundial", 65536),
}


def protocol_settings(protocol: str) -> ProtocolSettings:
    try:
        return PROTOCOLS[protocol]
    except KeyError as error:
        raise ValidationError(f"fig7: unsupported protocol: {protocol}") from error


def build_ycsb_argv(
    *,
    node_id: int,
    servers: str,
    protocol: str,
    write_ratio_pct: int,
    dax_path: str,
    workers: int,
    run_seconds: int,
    warmup_seconds: int,
    keys: int = 300_000,
    zipf: float = 0.7,
    cross_ratio: int = 100,
    entry_num: int = 8192,
) -> list[str]:
    if write_ratio_pct < 0 or write_ratio_pct > 100:
        raise ValidationError("fig7: write ratio must be between 0 and 100")
    if entry_num <= 0:
        raise ValidationError("fig7: CXL transport entry count must be positive")
    settings = protocol_settings(protocol)
    return [
        "./bench_ycsb",
        "--logtostderr=1",
        f"--id={node_id}",
        f"--servers={servers}",
        f"--threads={workers}",
        "--partition_num=2",
        "--granule_count=2000",
        f"--protocol={settings.binary_protocol}",
        "--query=rmw",
        f"--keys={keys}",
        f"--read_write_ratio={100 - write_ratio_pct}",
        f"--zipf={zipf}",
        f"--cross_ratio={cross_ratio}",
        "--cross_part_num=2",
        "--cxl_backend=dax",
        f"--cxl_memory_resource={dax_path}",
        f"--time_to_run={run_seconds}",
        f"--time_to_warmup={warmup_seconds}",
        "--partitioner=hash",
        "--use_cxl_transport=1",
        "--use_output_thread=0",
        f"--cxl_trans_entry_struct_size={settings.transport_entry_size}",
        f"--cxl_trans_entry_num={entry_num}",
        "--log_path=/root/pasha_log",
        "--lotus_checkpoint=0",
        "--persist_latency=0",
        "--wal_group_commit_time=0",
        "--wal_group_commit_size=0",
        "--hstore_command_logging=false",
        "--replica_group=1",
        "--lock_manager=0",
        "--batch_flush=1",
        "--lotus_async_repl=true",
        "--batch_size=0",
        *settings.pasha_flags,
    ]


def run(args: argparse.Namespace) -> None:
    tigon_root = args.tigon_root.resolve()
    local_binary = tigon_root / "build/bench_ycsb"
    if not (tigon_root / "bench_ycsb.cpp").is_file() or not local_binary.is_file():
        raise ReproductionError(
            f"fig7: real Tigon tree or built bench_ycsb is unavailable: {tigon_root}"
        )
    if args.workers <= 0 or args.run_seconds <= 0 or args.warmup_seconds < 0:
        raise ValidationError("fig7: worker and duration settings are invalid")

    nodes = [(0, args.node0), (1, args.node1)]
    servers = f"{args.node0}:1234;{args.node1}:1234"
    remote = Remote(args.ssh_user, args.ssh_port, args.ssh_timeout)
    slug = args.protocol.lower().replace("+", "plus")
    output_paths = {
        node_id: (
            f"{args.guest_workdir}/fig7-{slug}-write-"
            f"{args.write_ratio:03d}-node-{node_id}.log"
        )
        for node_id, _ in nodes
    }

    try:
        for _, host in nodes:
            remote.run(host, "true")
            remote.run(
                host,
                build_guest_prepare_command(
                    args.dax_path, args.guest_workdir, "bench_ycsb"
                ),
            )
            sync_binary(
                remote,
                host,
                local_binary,
                args.guest_workdir,
                "bench_ycsb",
            )

        common = dict(
            servers=servers,
            protocol=args.protocol,
            write_ratio_pct=args.write_ratio,
            dax_path=args.dax_path,
            workers=args.workers,
            run_seconds=args.run_seconds,
            warmup_seconds=args.warmup_seconds,
            keys=args.keys,
            zipf=args.zipf,
            cross_ratio=args.cross_ratio,
            entry_num=args.cxl_entry_num,
        )
        argv_by_node = {
            node_id: build_ycsb_argv(node_id=node_id, **common)
            for node_id, _ in nodes
        }
        host_by_node = dict(nodes)
        owner_id, peer_id = tigon_launch_order()
        _launch(
            remote,
            host_by_node[owner_id],
            args.guest_workdir,
            output_paths[owner_id],
            argv_by_node[owner_id],
        )
        _wait_for_marker(
            remote,
            host_by_node[owner_id],
            output_paths[owner_id],
            "initializes CXL transport metadata",
            "bench_ycsb",
            args.init_timeout,
            args.poll_interval,
        )
        _launch(
            remote,
            host_by_node[peer_id],
            args.guest_workdir,
            output_paths[peer_id],
            argv_by_node[peer_id],
        )
        _wait_for_marker(
            remote,
            host_by_node[peer_id],
            output_paths[peer_id],
            "retrives CXL transport metadata",
            "bench_ycsb",
            args.init_timeout,
            args.poll_interval,
            reject_markers=(
                "CXL shared memory not available",
                "initialized local CXL transport metadata",
            ),
        )
        outputs = {
            node_id: _wait_for_marker(
                remote,
                host,
                output_paths[node_id],
                "average commit:",
                "bench_ycsb",
                args.done_timeout,
                args.poll_interval,
            )
            for node_id, host in nodes
        }
        throughput = sum(parse_average_commit(outputs[node_id]) for node_id, _ in nodes)
        print(f"YCSB throughput={throughput:.6f} txn/s")
    finally:
        for _, host in nodes:
            try:
                remote.run(host, "pkill -TERM bench_ycsb 2>/dev/null || true", check=False)
            except (OSError, subprocess.SubprocessError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tigon-root", type=Path, required=True)
    parser.add_argument("--protocol", choices=tuple(PROTOCOLS), required=True)
    parser.add_argument("--write-ratio", type=int, required=True)
    parser.add_argument("--node0", default="192.168.100.10")
    parser.add_argument("--node1", default="192.168.100.11")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--ssh-timeout", type=int, default=300)
    parser.add_argument("--guest-workdir", default="/root/pasha")
    parser.add_argument("--dax-path", default="/dev/dax0.0")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run-seconds", type=int, default=10)
    parser.add_argument("--warmup-seconds", type=int, default=2)
    parser.add_argument("--keys", type=int, default=300_000)
    parser.add_argument("--zipf", type=float, default=0.7)
    parser.add_argument("--cross-ratio", type=int, default=100)
    parser.add_argument("--cxl-entry-num", type=int, default=8192)
    parser.add_argument("--init-timeout", type=int, default=300)
    parser.add_argument("--done-timeout", type=int, default=180)
    parser.add_argument("--poll-interval", type=float, default=1.0)
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
