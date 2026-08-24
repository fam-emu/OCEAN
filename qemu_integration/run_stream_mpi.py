#!/usr/bin/env python3
"""Boot two CXL VMs, provision STREAM, and run it across both nodes."""

import argparse
import os
import shutil
import signal
import subprocess
import re
import time
from dataclasses import dataclass

import pexpect

from cxl_vm_test_framework import (
    CxlVmFramework,
    StepError,
    VM,
    ensure_host_password_ssh,
    ensure_hostfile,
    ensure_peer_ssh as ensure_peer,
    guest_sha256,
    preflight_cxl_test,
    reap_stale_cxl_processes as reap_stale_processes,
    section,
    scp_to_guest,
    sha256,
    start_cxlmemsim_server,
    step_fail,
    step_pass,
    stop_cxl_test_environment,
    warn,
)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(REPO_ROOT, "build")
QEMU_BINARY = os.path.join(REPO_ROOT, "lib", "qemu", "build", "qemu-system-x86_64")
LAUNCHERS = (
    ("VM0", "../qemu_integration/launch_qemu_cxl.sh", "stream_mpi_vm0.log", "192.168.100.10"),
    ("VM1", "../qemu_integration/launch_qemu_cxl1.sh", "stream_mpi_vm1.log", "192.168.100.11"),
)
STREAM_DIR = os.path.join(REPO_ROOT, "workloads", "stream")
STREAM_SOURCE = os.path.join(STREAM_DIR, "stream_mpi.c")
SHIM = "/root/libmpi_cxl_shim.so"


@dataclass(frozen=True)
class StreamWorkload:
    binary_name: str
    array_size: int

    @property
    def host_binary(self):
        return os.path.join(STREAM_DIR, self.binary_name)

    @property
    def guest_binary(self):
        return "/root/%s" % self.binary_name


STREAM_WORKLOADS = {
    "small": StreamWorkload("stream_mpi_small", 1_000_000),
    "medium": StreamWorkload("stream_mpi_medium", 10_000_000),
    "big": StreamWorkload("stream_mpi_large", 80_000_000),
}


def stream_workload(size):
    return STREAM_WORKLOADS[size]


def build_stream_binary(workload):
    if os.path.isfile(workload.host_binary) and os.access(workload.host_binary, os.X_OK):
        return
    if shutil.which("mpicc") is None:
        step_fail("mpicc is required to build STREAM on the host")
    try:
        subprocess.run(
            [
                "mpicc", "-O3", "-fopenmp", "-DSTREAM_ARRAY_SIZE=%d" % workload.array_size,
                "-o", workload.binary_name, "stream_mpi.c",
            ],
            cwd=STREAM_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        step_fail("STREAM compilation failed (rc=%d)" % error.returncode)
    if not os.path.isfile(workload.host_binary) or not os.access(workload.host_binary, os.X_OK):
        step_fail("STREAM compilation did not create an executable: %s" % workload.host_binary)
    step_pass("built %s with STREAM_ARRAY_SIZE=%d" % (workload.binary_name, workload.array_size))


def sync_stream_binary(vms, workload=None):
    workload = workload or stream_workload("small")
    build_stream_binary(workload)
    host_sha = sha256(workload.host_binary)
    outdated = [
        (vm, address) for vm, address in vms if guest_sha256(vm, workload.guest_binary) != host_sha
    ]
    if not outdated:
        step_pass("%s matches the host build on both VMs" % workload.binary_name)
        return
    copied = []
    for vm, address in outdated:
        scp_to_guest(workload.host_binary, workload.guest_binary, address, vm.name)
        if guest_sha256(vm, workload.guest_binary) != host_sha:
            step_fail("%s checksum mismatch after copying %s" % (vm.name, workload.binary_name), vm.recent())
        vm.run_checked("chmod 755 %s" % workload.guest_binary)
        copied.append(vm.name)
    step_pass("copied %s to %s" % (workload.binary_name, ", ".join(copied)))


def preflight(capacity):
    required = [
        QEMU_BINARY,
        STREAM_SOURCE,
        os.path.join(REPO_ROOT, "qemu_integration", "launch_qemu_cxl.sh"),
        os.path.join(REPO_ROOT, "qemu_integration", "launch_qemu_cxl1.sh"),
        os.path.join(REPO_ROOT, "qemu_integration", "cxl-numa-setup.service"),
        os.path.join(REPO_ROOT, "qemu_integration", "fixed-numa-setup.sh"),
        os.path.join(REPO_ROOT, "qemu_integration", "enable-cxl-system-ram.sh"),
    ]
    preflight_cxl_test(
        capacity,
        required,
        [(BUILD_DIR, image) for image in ("qemu.img", "qemu1.img", "bzImage", "cxlmemsim_server")],
        "preflight complete (capacity=%d MiB; STREAM source ready)",
    )


def stream_command(workload=None):
    workload = workload or stream_workload("small")
    return (
        "unset CXL_SHIM_VERBOSE CXL_SHIM_TRACE; "
        "export CXL_DAX_PATH=/dev/dax0.0 CXL_DAX_RESET=1; "
        "LD_PRELOAD=%s mpirun --allow-run-as-root -np 2 -hostfile hostfile "
        "-x CXL_DAX_PATH -x CXL_DAX_RESET -x LD_PRELOAD %s; echo STREAM_RC=$?"
        % (SHIM, workload.guest_binary)
    )


def run_stream(node0, workload=None):
    workload = workload or stream_workload("small")
    prerequisites = node0.run("test -x %s && test -r %s && echo ready || echo missing" % (workload.guest_binary, SHIM))
    if prerequisites.splitlines()[-1:] != ["ready"]:
        step_fail("STREAM executable or CXL MPI shim is missing on node0", node0.recent())
    section("STREAM MPI output")
    output = node0.run(stream_command(workload), timeout=660)
    print(output, flush=True)
    return_codes = re.findall(r"STREAM_RC=(\d+)", output)
    if not return_codes or return_codes[-1] != "0":
        step_fail("STREAM MPI did not complete successfully (rc=%s)" %
                  (return_codes[-1] if return_codes else "unknown"), node0.recent())
    if "Function" not in output or "Triad" not in output:
        step_fail("STREAM MPI completed without its bandwidth table", node0.recent())
    step_pass("STREAM MPI completed across both CXL VMs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity", type=int, default=2048)
    parser.add_argument("--mode", choices=("kvm", "kvm-direct", "tcg"), default="kvm-direct")
    parser.add_argument("--latency", type=int, default=70)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--dax-timeout", type=int, default=1320)
    parser.add_argument("--size", choices=tuple(STREAM_WORKLOADS), default="small",
                        help="STREAM array size to run (default: small)")
    parser.add_argument("--repair-guest-config", action="store_true")
    parser.add_argument("--reap-stale-state", action="store_true")
    args = parser.parse_args()
    workload = stream_workload(args.size)

    vms, server, server_log = [], None, None
    cleaned = False

    def cleanup():
        nonlocal cleaned
        if not cleaned:
            cleaned = True
            stop_cxl_test_environment(vms, server, server_log)

    def on_signal(_signum, _frame):
        cleanup()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    try:
        preflight(args.capacity)
        if args.reap_stale_state:
            reap_stale_processes()
        server, server_log = start_cxlmemsim_server(BUILD_DIR, args.capacity, args.latency, "stream_mpi_server.log")
        environment = os.environ.copy()
        environment.update(QEMU_BINARY=QEMU_BINARY, CXL_CAPACITY_MB=str(args.capacity))
        stream_vms = []
        for name, launcher, log_name, address in LAUNCHERS:
            vm = VM(name, launcher, environment, os.path.join(BUILD_DIR, log_name), args.mode, BUILD_DIR)
            vms.append(vm)
            stream_vms.append((vm, address))
            vm.wait_login(args.boot_timeout)
            vm.login()
            step_pass("%s booted and logged in" % name)
        framework = CxlVmFramework(REPO_ROOT, args.repair_guest_config, args.capacity)
        for index, vm in enumerate(vms):
            framework.validate_pre_dax(vm, index, args.dax_timeout)
        framework.wait_for_dax_group(vms, args.dax_timeout)
        sync_stream_binary(stream_vms, workload)
        ensure_host_password_ssh("node0", "192.168.100.10")
        ensure_hostfile(vms[0], check_write=True)
        ensure_peer(vms[0])
        run_stream(vms[0], workload)
        print("All CXL setup and STREAM MPI checks passed.", flush=True)
    except (StepError, pexpect.ExceptionPexpect, subprocess.SubprocessError) as error:
        warn(str(error))
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
