#!/usr/bin/env python3
"""Boot two CXL VMs and verify OSU Allgather completes across them."""

import argparse
import os
import signal
import subprocess
import time

import pexpect

from cxl_vm_test_framework import (
    CxlVmFramework,
    StepError,
    VM,
    ensure_host_password_ssh,
    ensure_hostfile,
    ensure_peer_ssh as ensure_peer,
    hostfile_validation_command,
    password_ssh_arguments,
    preflight_cxl_test,
    reap_stale_cxl_processes as reap_stale_processes,
    section,
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
    ("VM0", "../qemu_integration/launch_qemu_cxl.sh", "osu_run_vm0.log"),
    ("VM1", "../qemu_integration/launch_qemu_cxl1.sh", "osu_run_vm1.log"),
)
OSU_BIN = "~/osu-micro-benchmarks/mpi/collective/osu_allgather"
SHIM = "/root/libmpi_cxl_shim.so"


def preflight(capacity):
    required = [
        BUILD_DIR,
        QEMU_BINARY,
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
        "preflight complete (capacity=%d MiB)",
    )


def osu_command():
    return (
        "unset CXL_SHIM_VERBOSE CXL_SHIM_TRACE; "
        "export CXL_DAX_PATH=/dev/dax0.0 CXL_DAX_RESET=1; "
        "LD_PRELOAD=%s mpirun --allow-run-as-root -np 2 -hostfile hostfile "
        "-x CXL_DAX_PATH -x CXL_DAX_RESET -x LD_PRELOAD %s -m 2:16384; echo OSU_RC=$?" % (SHIM, OSU_BIN)
    )


def run_osu(node0):
    prerequisites = node0.run("test -x %s && test -r %s && echo ready || echo missing" % (OSU_BIN, SHIM))
    if prerequisites.splitlines()[-1:] != ["ready"]:
        step_fail("OSU executable or CXL MPI shim is missing on node0", node0.recent())
    command = osu_command()
    started = time.time()
    section("OSU allgather output")
    output = node0.run(command, timeout=660)
    print(output, flush=True)
    return_codes = re.findall(r"OSU_RC=(\d+)", output)
    if not return_codes or return_codes[-1] != "0":
        step_fail("OSU allgather did not complete successfully (rc=%s)" %
                  (return_codes[-1] if return_codes else "unknown"), node0.recent())
    if "Avg Latency" not in output:
        step_fail("OSU allgather completed without its latency table", node0.recent())
    step_pass("OSU allgather completed in %.0f seconds" % (time.time() - started))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity", type=int, default=2048)
    parser.add_argument("--mode", choices=("kvm", "kvm-direct", "tcg"), default="kvm-direct")
    parser.add_argument("--latency", type=int, default=70)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--dax-timeout", type=int, default=1320)
    parser.add_argument("--repair-guest-config", action="store_true",
                        help="repair guest CXL files, SSH policy, and VM identity before validation")
    parser.add_argument("--reap-stale-state", action="store_true",
                        help="kill stale matching QEMU/server processes and remove their named shared-memory files")
    args = parser.parse_args()

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
        server, server_log = start_cxlmemsim_server(BUILD_DIR, args.capacity, args.latency, "osu_run_server.log")
        environment = os.environ.copy()
        environment.update(QEMU_BINARY=QEMU_BINARY, CXL_CAPACITY_MB=str(args.capacity))
        for name, launcher, log_name in LAUNCHERS:
            vm = VM(name, launcher, environment, os.path.join(BUILD_DIR, log_name), args.mode, BUILD_DIR)
            vms.append(vm)
            vm.wait_login(args.boot_timeout)
            vm.login()
            step_pass("%s booted and logged in" % name)

        framework = CxlVmFramework(REPO_ROOT, args.repair_guest_config, args.capacity)
        for index, vm in enumerate(vms):
            framework.validate_pre_dax(vm, index, args.dax_timeout)
        framework.wait_for_dax_group(vms, args.dax_timeout)

        ensure_host_password_ssh("node0", "192.168.100.10")
        ensure_hostfile(vms[0])
        ensure_peer(vms[0])
        run_osu(vms[0])
        print("All CXL setup and OSU allgather checks passed.", flush=True)
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
