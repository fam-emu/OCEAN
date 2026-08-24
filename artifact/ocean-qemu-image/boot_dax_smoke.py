#!/usr/bin/env python3
"""Boot a candidate Packer image through DAX and SSH readiness only.

This runner deliberately uses artifacts and logs below artifact/ocean-qemu-image.  It
never reads, writes, or replaces build/qemu.img or build/bzImage.
"""

import argparse
import dataclasses
import os
import pathlib
import re
import subprocess
import sys
import time

import pexpect


HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
ROOT_PASSWORD = "victor129"
PROMPT = "OCEAN_PACKER_SMOKE# "


@dataclasses.dataclass(frozen=True)
class Artifacts:
    image: pathlib.Path
    kernel: pathlib.Path


def default_artifacts():
    """Return the only artifact locations used by the smoke test."""
    output = HERE / "disk-image"
    return Artifacts(image=output / "qemu.img", kernel=output / "bzImage")


def qemu_command(qemu_binary, qemu_data_dir, artifacts, cxl_capacity_mib, cxl_backing_file, lsa_backing_file):
    """Build the one-VM candidate-image QEMU command."""
    return [
        qemu_binary,
        "--enable-kvm", "-cpu", "qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt",
        "-L", qemu_data_dir,
        "-m", "16G,maxmem=32G,slots=8",
        "-smp", "2",
        "-M", "q35,cxl=on",
        "-kernel", str(artifacts.kernel),
        "-append", "root=/dev/sda1 rw console=ttyS0,115200 nokaslr cxl_region_mb=%d" % cxl_capacity_mib,
        "-drive", "file=%s,index=0,media=disk,format=raw" % artifacts.image,
        "-netdev", "tap,id=net0,ifname=tap0,script=no,downscript=no",
        "-device", "virtio-net-pci,netdev=net0,mac=52:54:00:00:00:01",
        "-fsdev", "local,security_model=none,id=fsdev0,path=/dev/shm",
        "-device", "virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshm,bus=pcie.0",
        "-device", "pxb-cxl,bus_nr=12,bus=pcie.0,id=cxl.1",
        "-device", "cxl-rp,port=0,bus=cxl.1,id=root_port13,chassis=0,slot=0",
        "-device", "cxl-rp,port=1,bus=cxl.1,id=root_port14,chassis=0,slot=1",
        "-device", "cxl-type3,bus=root_port13,persistent-memdev=cxl-mem1,lsa=cxl-lsa1,id=cxl-pmem0,sn=0x1",
        "-device", "cxl-type1,bus=root_port14,size=1G,cache-size=64M",
        "-device", "virtio-cxl-accel-pci,bus=pcie.0",
        "-object", "memory-backend-file,id=cxl-mem1,share=on,mem-path=%s,size=%dM" %
        (cxl_backing_file, cxl_capacity_mib),
        "-object", "memory-backend-file,id=cxl-lsa1,share=on,mem-path=%s,size=256K" % lsa_backing_file,
        "-M", "cxl-fmw.0.targets.0=cxl.1,cxl-fmw.0.size=4G",
        "-nographic",
    ]


def server_command(server, capacity_mib, latency_ns, lsa_backing_file, port, pgas_shm_name):
    """Build a simulator command sharing the candidate image's LSA file."""
    return [
        server,
        "--capacity=%d" % capacity_mib,
        "--default_latency=%d" % latency_ns,
        "--lsa-backing-file=%s" % lsa_backing_file,
        "--port=%d" % port,
        "--pgas-shm-name=%s" % pgas_shm_name,
    ]


def should_start_simulator(repair_only):
    return not repair_only


def should_verify_host_ssh(skip_host_ssh):
    return not skip_host_ssh


def require_file(path, label):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("%s is missing or empty: %s" % (label, path))


def run_guest(vm, command, timeout=60):
    vm.sendline(command)
    vm.expect_exact(PROMPT, timeout=timeout)
    return vm.before.replace("\r", "")


def serial_console_text(output):
    """Remove terminal control sequences from captured serial-console output."""
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)


def guest_command_succeeded(output):
    """Return whether the guest emitted a successful command-status marker."""
    cleaned_output = serial_console_text(output)
    return re.search(r"(?:^|\n)__OCEAN_PACKER_STATUS=0(?:\n|$)", cleaned_output) is not None


def run_guest_checked(vm, command, timeout=60):
    marker = "__OCEAN_PACKER_STATUS="
    output = run_guest(vm, "%s; status=$?; printf '%s%%s\\n' \"$status\"" % (command, marker), timeout)
    if not guest_command_succeeded(output):
        raise RuntimeError("guest repair command failed: %s" % output.strip())
    return output


def login(vm, timeout):
    vm.expect(r"(?:[A-Za-z0-9][A-Za-z0-9.-]* )?[Ll]ogin:", timeout=timeout)
    vm.sendline("root")
    vm.expect(r"[Pp]assword:", timeout=60)
    vm.sendline(ROOT_PASSWORD)
    vm.expect(r"# ", timeout=60)
    vm.sendline("export PS1='%s'" % PROMPT)
    vm.expect_exact(PROMPT, timeout=30)
    vm.expect_exact(PROMPT, timeout=30)


def wait_for_dax(vm, capacity_mib, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        device = run_guest(vm, "test -c /dev/dax0.0 && echo dax-ready || true").strip()
        service = run_guest(vm, "systemctl is-active cxl-numa-setup.service 2>/dev/null || true").strip()
        if device.endswith("dax-ready") and service.endswith("active"):
            print("[ PASS ] DAX provisioned and cxl-numa-setup.service is active", flush=True)
            return
        print("[ WAIT ] DAX=%s service=%s" % (device or "missing", service or "unknown"), flush=True)
        time.sleep(15)
    raise RuntimeError("/dev/dax0.0 did not become ready within %d seconds" % timeout)


def verify_ssh_policy(vm):
    policy = run_guest(vm, "sshd -T 2>/dev/null | grep -E '^(permitrootlogin|passwordauthentication) ' || true")
    required = {"permitrootlogin yes", "passwordauthentication yes"}
    if not required.issubset(set(serial_console_text(policy).lower().splitlines())):
        raise RuntimeError("guest SSH policy does not permit root password login: %s" % policy.strip())
    print("[ PASS ] guest SSH policy permits root password login", flush=True)


def guest_repair_command():
    """Return the persistent SSH and root-filesystem repair command."""
    return (
        "set -eu; "
        "mkdir -p /etc/ssh/sshd_config.d /run/sshd; "
        "printf 'PermitRootLogin yes\\nPasswordAuthentication yes\\n' > /etc/ssh/sshd_config.d/00-ocean-packer-smoke.conf; "
        "chmod 600 /etc/ssh/sshd_config.d/00-ocean-packer-smoke.conf; "
        "sshd -t; systemctl enable ssh; systemctl restart ssh; "
        "disk_bytes=$(blockdev --getsize64 /dev/sda); part_bytes=$(blockdev --getsize64 /dev/sda1); "
        "if [ $((disk_bytes - part_bytes)) -gt $((4 * 1024 * 1024)) ]; then "
        "printf ',+\\n' | sfdisk --no-reread -N 1 /dev/sda; "
        "partx -u /dev/sda; resize2fs /dev/sda1; "
        "fi"
    )


def repair_guest_config(vm):
    run_guest_checked(vm, guest_repair_command(), timeout=180)
    verify_ssh_policy(vm)
    print("[ PASS ] candidate SSH policy and root filesystem repair completed", flush=True)


def verify_host_ssh(address, timeout):
    child = pexpect.spawn(
        "ssh",
        ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", "-o", "PasswordAuthentication=yes",
         "-o", "NumberOfPasswordPrompts=1", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "ConnectTimeout=10", "root@%s" % address, "true"],
        encoding="utf-8", codec_errors="replace", timeout=timeout,
    )
    try:
        result = child.expect([r"[Pp]assword:", pexpect.EOF], timeout=timeout)
        if result == 0:
            child.sendline(ROOT_PASSWORD)
            child.expect(pexpect.EOF, timeout=timeout)
    finally:
        child.close()
    if child.exitstatus != 0:
        raise RuntimeError("host SSH failed: %s" % child.before)
    print("[ PASS ] host SSH root login succeeded", flush=True)


def terminate(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    defaults = default_artifacts()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=pathlib.Path, default=defaults.image)
    parser.add_argument("--kernel", type=pathlib.Path, default=defaults.kernel)
    parser.add_argument("--qemu-binary", default=str(REPO_ROOT / "lib/qemu/build/qemu-system-x86_64"))
    parser.add_argument("--qemu-data-dir", default=str(REPO_ROOT / "lib/qemu/pc-bios"))
    parser.add_argument("--server", default=str(REPO_ROOT / "build/cxlmemsim_server"))
    parser.add_argument("--capacity", type=int, default=1024)
    parser.add_argument("--latency", type=int, default=70)
    parser.add_argument("--server-port", type=int, default=19123)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--dax-timeout", type=int, default=1320)
    parser.add_argument("--ssh-address", default="192.168.100.10")
    parser.add_argument("--repair-guest-config", action="store_true",
                        help="persistently repair root-password SSH and grow /dev/sda1 after image expansion")
    parser.add_argument("--repair-only", action="store_true",
                        help="apply --repair-guest-config without starting the CXL simulator or checking DAX")
    parser.add_argument("--skip-host-ssh", action="store_true")
    args = parser.parse_args()

    artifacts = Artifacts(args.image.resolve(), args.kernel.resolve())
    require_file(artifacts.image, "candidate image")
    require_file(artifacts.kernel, "candidate kernel")
    require_file(pathlib.Path(args.qemu_binary), "QEMU binary")
    require_file(pathlib.Path(args.server), "CXL simulator")
    if args.capacity <= 0:
        raise SystemExit("--capacity must be positive")
    if args.repair_only and not args.repair_guest_config:
        raise SystemExit("--repair-only requires --repair-guest-config")

    state = HERE / "smoke-state"
    state.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    server_log = state / ("server-%s.log" % stamp)
    console_log = state / ("console-%s.log" % stamp)
    cxl_backing = state / "cxl-memory.raw"
    lsa_backing = state / "cxl-lsa.raw"
    pgas_shm_name = "/ocean-packer-smoke-%d" % os.getpid()
    for path in (cxl_backing, lsa_backing):
        path.unlink(missing_ok=True)

    server = vm = None
    try:
        with server_log.open("wb") as log:
            if should_start_simulator(args.repair_only):
                server = subprocess.Popen(
                    server_command(args.server, args.capacity, args.latency, lsa_backing, args.server_port, pgas_shm_name),
                    cwd=REPO_ROOT / "build", stdout=log, stderr=subprocess.STDOUT,
                )
                time.sleep(2)
                if server.poll() is not None:
                    raise RuntimeError("CXL simulator exited; see %s" % server_log)
                print("[ PASS ] simulator started", flush=True)
            else:
                print("[ PASS ] repair-only mode skipped the CXL simulator", flush=True)

            command = qemu_command(args.qemu_binary, args.qemu_data_dir, artifacts, args.capacity,
                                   cxl_backing, lsa_backing)
            with console_log.open("w", encoding="utf-8") as log:
                qemu_environment = os.environ.copy()
                qemu_environment.update(CXL_MEMSIM_HOST="127.0.0.1", CXL_MEMSIM_PORT=str(args.server_port),
                                        CXL_TRANSPORT_MODE="tcp", CXL_HOST_ID="0")
                vm = pexpect.spawn(command[0], command[1:], cwd=HERE, encoding="utf-8", codec_errors="replace",
                                  timeout=args.boot_timeout, env=qemu_environment)
                vm.logfile_read = log
                login(vm, args.boot_timeout)
                print("[ PASS ] serial root login succeeded", flush=True)
                if args.repair_guest_config:
                    repair_guest_config(vm)
                verify_ssh_policy(vm)
                if args.repair_only:
                    if should_verify_host_ssh(args.skip_host_ssh):
                        verify_host_ssh(args.ssh_address, 30)
                    print("[ PASS ] candidate guest repair completed", flush=True)
                    return 0
                wait_for_dax(vm, args.capacity, args.dax_timeout)
                if should_verify_host_ssh(args.skip_host_ssh):
                    verify_host_ssh(args.ssh_address, 30)
        print("[ PASS ] candidate Packer image boot/DAX/SSH smoke test completed", flush=True)
        return 0
    except (pexpect.ExceptionPexpect, OSError, RuntimeError) as error:
        print("[ FAIL ] %s" % error, file=sys.stderr, flush=True)
        print("Logs: %s and %s" % (console_log, server_log), file=sys.stderr, flush=True)
        return 1
    finally:
        if vm:
            try:
                # The console logfile context may already be closed when QEMU
                # exits before login (for example, when KVM is unavailable).
                vm.logfile_read = None
                if vm.isalive():
                    vm.sendline("shutdown -h now || poweroff -f || true")
                    vm.expect(pexpect.EOF, timeout=15)
            except (pexpect.ExceptionPexpect, OSError):
                pass
            vm.close(force=True)
        terminate(server)


if __name__ == "__main__":
    raise SystemExit(main())
