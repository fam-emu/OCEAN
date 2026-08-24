#!/usr/bin/env python3
"""Run the one-VM CXL allocator lat_mem_rd regression."""

import argparse
import os
import re
import signal
import subprocess

import pexpect

from cxl_vm_test_framework import (
    COMMAND_STATUS,
    CxlVmFramework,
    StepError,
    VM,
    command_result,
    guest_sha256,
    password_scp_arguments as scp_arguments,
    preflight_cxl_test,
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
LAUNCHER = "../qemu_integration/launch_qemu_cxl.sh"
LMBENCH_MAKEFILE = os.path.join(REPO_ROOT, "workloads", "lmbench", "src", "Makefile")
LAT_MEM_RD = os.path.join(REPO_ROOT, "workloads", "lmbench", "bin", "x86_64-linux-gnu", "lat_mem_rd")
PRELOAD = os.path.join(REPO_ROOT, "workloads", "cxlalloc", "target", "release", "libcxlalloc_preload.so")
GUEST_LAT_MEM_RD = "/root/lat_mem_rd"
GUEST_PRELOAD = "/root/libcxlalloc_preload_release.so"
LAT_MEM_RD_FINAL_SIZE_MIB = 128.0
LAT_MEM_RD_RESULT_ROW = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*$", re.MULTILINE)


def dax_timeout_for_capacity(capacity_mib, requested_timeout):
    """Return an explicit timeout or a conservative capacity-based default."""
    if requested_timeout is not None:
        return requested_timeout
    return max(1320, (capacity_mib * 600 + 1023) // 1024)


def has_lmbench_x86_64_baseline(makefile_text):
    return bool(re.search(r"^COMPILE=.*(?:^|\s)-march=x86-64(?:\s|$)", makefile_text, re.MULTILINE))


def is_executable_file(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def artifact_needs_copy(host_sha, guest_sha):
    return host_sha != guest_sha


def build_workload_command(lat_mem_rd, preload):
    return (
        "cd /root && CXLALLOC_BACKEND=dax CXLALLOC_HEAP_SIZE=3221225472 "
        "LD_PRELOAD=./%s ./%s -t -N 2 1024 64"
        % (os.path.basename(preload), os.path.basename(lat_mem_rd))
    )


def build_workload_section(lat_mem_rd, preload):
    return (
        "Executing CXLALLOC_BACKEND=dax CXLALLOC_HEAP_SIZE=3221225472 "
        "LD_PRELOAD=./%s ./%s -t -N 2 256 64"
        % (os.path.basename(preload), os.path.basename(lat_mem_rd))
    )


def has_completed_lmbench_sweep(output):
    """Return whether lat_mem_rd emitted its final 128 MiB result row."""
    return any(
        float(size_mib) == LAT_MEM_RD_FINAL_SIZE_MIB
        for size_mib, _latency in LAT_MEM_RD_RESULT_ROW.findall(output)
    )


def sync_artifacts(vm):
    artifacts = ((LAT_MEM_RD, GUEST_LAT_MEM_RD), (PRELOAD, GUEST_PRELOAD))
    copied = []
    for source, destination in artifacts:
        host_sha = sha256(source)
        if artifact_needs_copy(host_sha, guest_sha256(vm, destination)):
            scp_to_guest(source, destination, "192.168.100.10", "VM0")
            if guest_sha256(vm, destination) != host_sha:
                step_fail("VM0 checksum mismatch after copying %s" % os.path.basename(source), vm.recent())
            copied.append(os.path.basename(source))
    vm.run_checked("chmod 755 %s" % GUEST_LAT_MEM_RD)
    if copied:
        step_pass("copied changed workload artifacts to VM0: %s" % ", ".join(copied))
    else:
        step_pass("VM0 workload artifacts already match host checksums")


def preflight(capacity):
    required = [
        QEMU_BINARY,
        os.path.join(REPO_ROOT, "qemu_integration", "launch_qemu_cxl.sh"),
        os.path.join(REPO_ROOT, "qemu_integration", "cxl-numa-setup.service"),
        os.path.join(REPO_ROOT, "qemu_integration", "fixed-numa-setup.sh"),
        os.path.join(REPO_ROOT, "qemu_integration", "enable-cxl-system-ram.sh"),
        LAT_MEM_RD,
        PRELOAD,
    ]
    preflight_cxl_test(
        capacity,
        required,
        [(BUILD_DIR, image) for image in ("qemu.img", "bzImage", "cxlmemsim_server")],
        "preflight complete (capacity=%d MiB; LMBench x86-64 and release preload ready)",
    )
    if not is_executable_file(LAT_MEM_RD):
        step_fail("LMBench lat_mem_rd is not executable: %s" % LAT_MEM_RD)
    with open(LMBENCH_MAKEFILE, encoding="utf-8") as makefile:
        if not has_lmbench_x86_64_baseline(makefile.read()):
            step_fail("LMBench must compile with GCC's -march=x86-64 baseline")


def run_workload(vm, mode, timeout):
    section(build_workload_section(GUEST_LAT_MEM_RD, GUEST_PRELOAD))
    command = "%s; status=$?; printf '%s%%s\\n' \"$status\"" % (
        build_workload_command(GUEST_LAT_MEM_RD, GUEST_PRELOAD), COMMAND_STATUS
    )
    output = vm.run(command, timeout=timeout)
    visible, status = command_result(output)
    print(visible, flush=True)
    if status != 0:
        step_fail("lat_mem_rd failed in %s mode (rc=%s)" % (mode, status if status is not None else "unknown"), vm.recent())
    if not has_completed_lmbench_sweep(visible):
        step_fail("lat_mem_rd did not complete the sweep through 128 MiB", visible)
    step_pass("lat_mem_rd completed the sweep through 128 MiB in %s mode" % mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity", type=int, default=4096)
    parser.add_argument("--mode", choices=("kvm", "kvm-direct", "tcg"), default="kvm")
    parser.add_argument("--latency", type=int, default=70)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--dax-timeout", type=int,
                        help="seconds to wait for /dev/dax0.0 (default scales with --capacity)")
    parser.add_argument("--workload-timeout", type=int, default=8 * 60 * 60)
    parser.add_argument("--repair-guest-config", action="store_true")
    args = parser.parse_args()
    dax_timeout = dax_timeout_for_capacity(args.capacity, args.dax_timeout)

    vm = server = server_log = None
    cleaned = False

    def cleanup():
        nonlocal cleaned
        if not cleaned:
            cleaned = True
            stop_cxl_test_environment([vm] if vm else [], server, server_log)

    def on_signal(_signum, _frame):
        cleanup()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    try:
        preflight(args.capacity)
        server, server_log = start_cxlmemsim_server(BUILD_DIR, args.capacity, args.latency, "lat_mem_rd_server.log")
        environment = os.environ.copy()
        environment.update(QEMU_BINARY=QEMU_BINARY, CXL_CAPACITY_MB=str(args.capacity))
        vm = VM("VM0", LAUNCHER, environment, os.path.join(BUILD_DIR, "lat_mem_rd_vm0.log"), args.mode, BUILD_DIR)
        vm.wait_login(args.boot_timeout)
        vm.login()
        step_pass("VM0 booted and logged in")
        CxlVmFramework(REPO_ROOT, args.repair_guest_config, args.capacity).validate_boot(
            vm, 0, dax_timeout, dax_wait_hook=sync_artifacts
        )
        run_workload(vm, args.mode, args.workload_timeout)
        print("CXL allocator lat_mem_rd regression completed.", flush=True)
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
