#!/usr/bin/env python3
"""Run one two-node Tigon TPC-C point for Figure 6."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from figures_6_9.errors import ReproductionError, ValidationError


MAX_HCC_BYTES = 200 * 1024 * 1024
EBR_RESERVE_BYTES = 1 * 1024 * 1024
AVERAGE_COMMIT = re.compile(
    r"(?:^|\n).*?average commit:\s*(?P<throughput>[0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def hcc_budget_bytes(
    coverage_pct: int,
    maximum: int = MAX_HCC_BYTES,
    reserve: int = EBR_RESERVE_BYTES,
) -> int:
    if coverage_pct < 0 or coverage_pct > 100:
        raise ValidationError("fig6: coverage must be between 0 and 100")
    if maximum <= reserve or reserve < 0:
        raise ValidationError("fig6: maximum HCC budget must exceed the EBR reserve")
    return reserve + (maximum - reserve) * coverage_pct // 100


def tigon_launch_order() -> tuple[int, int]:
    """Match Tigon's owner-first shared-metadata initialization contract."""
    return (0, 1)


def parse_average_commit(text: str) -> float:
    matches = list(AVERAGE_COMMIT.finditer(text))
    if len(matches) != 1:
        raise ValidationError(
            f"fig6: expected one average commit summary, got {len(matches)}"
        )
    throughput = float(matches[0]["throughput"])
    if throughput <= 0:
        raise ValidationError("fig6: average commit throughput must be positive")
    return throughput


def build_tpcc_argv(
    *,
    node_id: int,
    servers: str,
    budget_bytes: int,
    dax_path: str,
    workers: int,
    run_seconds: int,
    warmup_seconds: int,
    query: str = "mixed",
) -> list[str]:
    if query not in {"mixed", "neworder", "payment", "first_two", "test"}:
        raise ValidationError(f"fig6: unsupported TPC-C query mode: {query}")
    partitions = 2 * workers
    return [
        "./bench_tpcc",
        "--logtostderr=1",
        f"--id={node_id}",
        f"--servers={servers}",
        f"--threads={workers}",
        f"--partition_num={partitions}",
        "--protocol=TwoPLPasha",
        f"--query={query}",
        "--neworder_dist=10",
        "--payment_dist=15",
        "--cxl_backend=dax",
        f"--cxl_memory_resource={dax_path}",
        f"--time_to_run={run_seconds}",
        f"--time_to_warmup={warmup_seconds}",
        "--partitioner=hash",
        "--granule_count=2000",
        "--use_cxl_transport=1",
        "--use_output_thread=0",
        "--cxl_trans_entry_struct_size=2048",
        "--cxl_trans_entry_num=8192",
        "--enable_migration_optimization=1",
        "--migration_policy=Clock",
        "--when_to_move_out=OnDemand",
        f"--hw_cc_budget={budget_bytes}",
        "--enable_scc=1",
        "--scc_mechanism=WriteThrough",
        "--pre_migrate=None",
        "--model_cxl_search_overhead=0",
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
    ]


class Remote:
    def __init__(self, user: str, port: int, timeout_s: int):
        password_mode = bool(os.environ.get("SSHPASS"))
        if password_mode and shutil.which("sshpass") is None:
            raise ReproductionError("fig6: SSHPASS is set but sshpass is unavailable")
        password_prefix = ["sshpass", "-e"] if password_mode else []
        common = [
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", f"ConnectTimeout={min(timeout_s, 30)}",
            "-p", str(port),
        ]
        if not password_mode:
            common[0:0] = ["-o", "BatchMode=yes"]
        self.ssh_prefix = [*password_prefix, "ssh", *common]
        self.scp_prefix = [
            *password_prefix,
            "scp",
            *common[:-2],
            "-P", str(port),
        ]
        self.user = user
        self.timeout_s = timeout_s

    def run(self, host: str, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.ssh_prefix, f"{self.user}@{host}", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=self.timeout_s,
            check=False,
        )
        if check and result.returncode != 0:
            raise ReproductionError(
                f"fig6: SSH command failed on {host} with exit {result.returncode}: "
                f"{result.stdout.strip()}"
            )
        return result

    def copy(self, source: Path, host: str, destination: str) -> None:
        result = subprocess.run(
            [*self.scp_prefix, str(source), f"{self.user}@{host}:{destination}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=max(self.timeout_s, 300),
            check=False,
        )
        if result.returncode != 0:
            raise ReproductionError(
                f"fig6: cannot copy Tigon binary to {host}: {result.stdout.strip()}"
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_binary(
    remote: Remote,
    host: str,
    local_binary: Path,
    guest_workdir: str,
    binary_name: str = "bench_tpcc",
) -> None:
    local_hash = _sha256(local_binary)
    guest_binary = f"{guest_workdir}/{binary_name}"
    check = remote.run(
        host,
        f"sha256sum {shlex.quote(guest_binary)} 2>/dev/null | cut -d' ' -f1",
        check=False,
    )
    if check.returncode == 0 and check.stdout.strip() == local_hash:
        return
    temporary = f"{guest_binary}.fig6.tmp"
    remote.copy(local_binary, host, temporary)
    remote.run(
        host,
        f"install -m 0755 {shlex.quote(temporary)} {shlex.quote(guest_binary)} "
        f"&& rm -f {shlex.quote(temporary)}",
    )
    verify = remote.run(host, f"sha256sum {shlex.quote(guest_binary)} | cut -d' ' -f1")
    if verify.stdout.strip() != local_hash:
        raise ReproductionError(f"Tigon {binary_name} hash mismatch on {host}")


def build_detached_command(
    guest_workdir: str,
    output_path: str,
    argv: list[str],
) -> str:
    tunables = "glibc.cpu.hwcaps=-AVX,-AVX2,-AVX512F,-AVX_Fast_Unaligned_Load"
    child = (
        f"exec env GLIBC_TUNABLES={shlex.quote(tunables)} {shlex.join(argv)} "
        f"> {shlex.quote(output_path)} 2>&1 < /dev/null"
    )
    return (
        f"cd {shlex.quote(guest_workdir)} && "
        f"rm -f {shlex.quote(output_path)} && "
        f"setsid -f sh -c {shlex.quote(child)}"
    )


def build_guest_prepare_command(
    dax_path: str,
    guest_workdir: str,
    process_name: str,
    log_path: str = "/root/pasha_log",
) -> str:
    return (
        f"test -c {shlex.quote(dax_path)} && "
        f"mkdir -p {shlex.quote(guest_workdir)} {shlex.quote(log_path)} && "
        f"{{ pkill -9 {shlex.quote(process_name)} 2>/dev/null || true; }}"
    )


def _launch(
    remote: Remote,
    host: str,
    guest_workdir: str,
    output_path: str,
    argv: list[str],
) -> None:
    remote.run(host, build_detached_command(guest_workdir, output_path, argv))


def _wait_for_marker(
    remote: Remote,
    host: str,
    output_path: str,
    marker: str,
    process_name: str,
    timeout_s: int,
    poll_s: float,
    reject_markers: tuple[str, ...] = (),
) -> str:
    deadline = time.monotonic() + timeout_s
    latest = ""
    while time.monotonic() < deadline:
        result = remote.run(host, f"cat {shlex.quote(output_path)} 2>/dev/null", check=False)
        latest = result.stdout
        for rejected in reject_markers:
            if rejected in latest:
                raise ReproductionError(
                    f"fig6: {process_name} entered forbidden fallback on {host}: "
                    f"{rejected!r}\n{latest[-4000:]}"
                )
        if marker in latest:
            return latest
        alive = remote.run(host, f"pgrep -x {shlex.quote(process_name)}", check=False)
        if alive.returncode != 0 and latest:
            raise ReproductionError(
                f"fig6: {process_name} exited on {host} before {marker!r}:\n{latest[-4000:]}"
            )
        time.sleep(poll_s)
    raise ReproductionError(
        f"fig6: timed out on {host} waiting for {marker!r}:\n{latest[-4000:]}"
    )


def run(args: argparse.Namespace) -> None:
    tigon_root = args.tigon_root.resolve()
    local_binary = tigon_root / "build/bench_tpcc"
    if not (tigon_root / "bench_tpcc.cpp").is_file() or not local_binary.is_file():
        raise ReproductionError(
            f"fig6: real Tigon tree or built bench_tpcc is unavailable: {tigon_root}"
        )
    if args.workers <= 0 or args.run_seconds <= 0 or args.warmup_seconds < 0:
        raise ValidationError("fig6: worker and duration settings are invalid")

    nodes = [(0, args.node0), (1, args.node1)]
    servers = f"{args.node0}:1234;{args.node1}:1234"
    budget = hcc_budget_bytes(args.coverage, args.max_hcc_bytes)
    remote = Remote(args.ssh_user, args.ssh_port, args.ssh_timeout)
    output_paths = {
        node_id: f"{args.guest_workdir}/fig6-coverage-{args.coverage:03d}-node-{node_id}.log"
        for node_id, _ in nodes
    }

    try:
        for _, host in nodes:
            remote.run(host, "true")
            remote.run(
                host,
                build_guest_prepare_command(
                    args.dax_path, args.guest_workdir, "bench_tpcc"
                ),
            )
            sync_binary(remote, host, local_binary, args.guest_workdir)

        argv0 = build_tpcc_argv(
            node_id=0,
            servers=servers,
            budget_bytes=budget,
            dax_path=args.dax_path,
            workers=args.workers,
            run_seconds=args.run_seconds,
            warmup_seconds=args.warmup_seconds,
            query=args.query,
        )
        argv1 = build_tpcc_argv(
            node_id=1,
            servers=servers,
            budget_bytes=budget,
            dax_path=args.dax_path,
            workers=args.workers,
            run_seconds=args.run_seconds,
            warmup_seconds=args.warmup_seconds,
            query=args.query,
        )
        host_by_node = dict(nodes)
        argv_by_node = {0: argv0, 1: argv1}
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
            "bench_tpcc",
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
            "bench_tpcc",
            args.init_timeout,
            args.poll_interval,
            reject_markers=(
                "CXL shared memory not available",
                "initialized local CXL transport metadata",
            ),
        )

        outputs = {}
        for node_id, host in nodes:
            outputs[node_id] = _wait_for_marker(
                remote,
                host,
                output_paths[node_id],
                "average commit:",
                "bench_tpcc",
                args.done_timeout,
                args.poll_interval,
            )
        for node_id, _ in nodes:
            throughput = parse_average_commit(outputs[node_id])
            print(f"node={node_id} NewOrder throughput={throughput:.6f} txn/s")
    finally:
        for _, host in nodes:
            try:
                remote.run(host, "pkill -TERM bench_tpcc 2>/dev/null || true", check=False)
            except (OSError, subprocess.SubprocessError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tigon-root", type=Path, required=True)
    parser.add_argument("--coverage", type=int, required=True)
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
    parser.add_argument(
        "--query",
        choices=("mixed", "neworder", "payment", "first_two", "test"),
        default="mixed",
        help="TPC-C mix; mixed matches Tigon scripts/run_hwcc_budget.sh",
    )
    parser.add_argument("--max-hcc-bytes", type=int, default=MAX_HCC_BYTES)
    parser.add_argument("--init-timeout", type=int, default=240)
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
