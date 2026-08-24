#!/usr/bin/env python3
"""Run OSU allgather between two disposable clones of the candidate image.

All writable artifacts stay below artifact/ocean-qemu-image.  In particular, this
runner never reads from or writes to build/qemu.img or build/bzImage.
"""

import argparse
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

import pexpect


HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "qemu_integration"))

from cxl_vm_test_framework import (  # noqa: E402
    ROOT_PASSWORD,
    StepError,
    dax_progress_line,
    ensure_host_password_ssh,
    info,
    scp_to_guest,
    step_fail,
    step_pass,
    warn,
)
from boot_dax_smoke import PROMPT, login, run_guest, serial_console_text, verify_ssh_policy  # noqa: E402


OSU_BIN = "/opt/osu/libexec/osu-micro-benchmarks/mpi/collective/osu_allgather"
OSU_SOURCE = "/root/gromacs-2025.3/build/osu-micro-benchmarks-7.5.2"
SHIM = "/root/libmpi_cxl_shim.so"
HOSTFILE = "/root/hostfile"
DAX_POLL_INTERVAL = 30
DAX_PROGRESS_INTERVAL = 60
DAX_SSH_PROBE_AFTER = 600
NODES = (
    ("VM0", "node0", "192.168.100.10", "tap0", "52:54:00:00:00:10"),
    ("VM1", "node1", "192.168.100.11", "tap1", "52:54:00:00:00:11"),
)


def default_shim_path():
    return REPO_ROOT / "workloads/gromacs/libmpi_cxl_shim.so"


def require_file(path, label):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("%s is missing or empty: %s" % (label, path))


def overlay_command(base_image, overlay):
    """Build the qemu-img command for a disposable raw-backed qcow2 overlay."""
    return ["qemu-img", "create", "-f", "qcow2", "-F", "raw", "-b", str(base_image), str(overlay)]


def create_overlay(qemu_img, base_image, overlay):
    overlay.unlink(missing_ok=True)
    command = overlay_command(base_image, overlay)
    command[0] = qemu_img
    subprocess.run(command, check=True, cwd=HERE)


def qemu_command(qemu_binary, qemu_data_dir, kernel, overlay, capacity_mib, cxl_backing, lsa_backing, tap, mac,
                 hostname="node0"):
    """Build one QEMU command using a candidate-image overlay."""
    return [
        qemu_binary,
        "--enable-kvm", "-cpu", "qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt",
        "-L", str(qemu_data_dir),
        "-m", "16G,maxmem=32G,slots=8", "-smp", "2", "-M", "q35,cxl=on",
        "-kernel", str(kernel),
        "-append", "root=/dev/sda1 rw console=ttyS0,115200 nokaslr cxl_region_mb=%d systemd.hostname=%s"
        % (capacity_mib, hostname),
        "-drive", "file=%s,index=0,media=disk,format=qcow2" % overlay,
        "-netdev", "tap,id=net0,ifname=%s,script=no,downscript=no" % tap,
        "-device", "virtio-net-pci,netdev=net0,mac=%s" % mac,
        "-fsdev", "local,security_model=none,id=fsdev0,path=/dev/shm",
        "-device", "virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshm,bus=pcie.0",
        "-device", "pxb-cxl,bus_nr=12,bus=pcie.0,id=cxl.1",
        "-device", "cxl-rp,port=0,bus=cxl.1,id=root_port13,chassis=0,slot=0",
        "-device", "cxl-rp,port=1,bus=cxl.1,id=root_port14,chassis=0,slot=1",
        "-device", "cxl-type3,bus=root_port13,persistent-memdev=cxl-mem1,lsa=cxl-lsa1,id=cxl-pmem0,sn=0x1",
        "-device", "cxl-type1,bus=root_port14,size=1G,cache-size=64M",
        "-device", "virtio-cxl-accel-pci,bus=pcie.0",
        "-object", "memory-backend-file,id=cxl-mem1,share=on,mem-path=%s,size=%dM" % (cxl_backing, capacity_mib),
        "-object", "memory-backend-file,id=cxl-lsa1,share=on,mem-path=%s,size=256K" % lsa_backing,
        "-M", "cxl-fmw.0.targets.0=cxl.1,cxl-fmw.0.size=4G",
        "-nographic",
    ]


def server_command(server, capacity_mib, latency_ns, lsa_backing, port, pgas_shm_name):
    return [
        server, "--capacity=%d" % capacity_mib, "--default_latency=%d" % latency_ns,
        "--lsa-backing-file=%s" % lsa_backing, "--port=%d" % port,
        "--pgas-shm-name=%s" % pgas_shm_name,
    ]


def qemu_environment(base_environment, server_port, host_id, capacity_mib):
    """Match the integration launchers' CXL initialization environment."""
    environment = base_environment.copy()
    environment.update(
        CXL_MEMSIM_HOST="127.0.0.1",
        CXL_MEMSIM_PORT=str(server_port),
        CXL_TRANSPORT_MODE="tcp",
        CXL_HOST_ID=str(host_id),
    )
    environment.setdefault("CXL_LATENCY_INJECT", "1")
    environment.setdefault("CXL_MEMSIM_BULK_ZERO_WRITES", "0")
    environment.setdefault("CXL_MEMSIM_PREZERO_ZERO_WRITES_NOOP", "0")
    environment.setdefault("CXL_MEMSIM_ZERO_RANGE", "0:%d" % (capacity_mib * 1024 * 1024 - 4096))
    return environment


def launch_all_then_login(nodes, launch, authenticate, vms=None):
    """Start every VM before serial-console logins can block the second launch."""
    if vms is None:
        vms = []
    for index, node in enumerate(nodes):
        vms.append(launch(index, node))
    for vm, node in zip(vms, nodes):
        authenticate(vm, node)
    return vms


def find_osu_command():
    """Validate and print the deterministic OSU install location."""
    return "test -x %s && printf '%%s\\n' %s" % (OSU_BIN, OSU_BIN)


def build_osu_command():
    """Build the absent MPI OSU binary in a disposable guest overlay."""
    return (
        "set -e; "
        "if test -x {binary}; then echo osu-ready; else "
        "test -d {source}; cd {source}; "
        "make distclean >/dev/null 2>&1 || true; "
        "./configure CC=mpicc CXX=mpicxx --prefix=/opt/osu; "
        "make -j$(nproc); make install; "
        "test -x {binary}; echo osu-ready; "
        "fi"
    ).format(binary=OSU_BIN, source=OSU_SOURCE)


def ensure_osu(vms):
    """Ensure both MPI ranks have the benchmark before their remote launch."""
    for vm, (name, _, _, _, _) in zip(vms, NODES):
        output = run_guest(vm, build_osu_command(), timeout=900)
        if serial_console_text(output).strip().splitlines()[-1:] != ["osu-ready"]:
            step_fail("could not build OSU allgather on %s" % name, output)
    step_pass("OSU allgather is available on both candidate overlays")


def find_osu(node0):
    output = run_guest(node0, find_osu_command())
    if OSU_BIN not in serial_console_text(output).splitlines():
        step_fail("OSU allgather executable is missing on node0: %s" % OSU_BIN, output)
    return OSU_BIN


def verify_shim(node0):
    output = run_guest(node0, "test -r %s && echo shim-ready || echo shim-missing" % SHIM)
    if serial_console_text(output).strip().splitlines()[-1:] != ["shim-ready"]:
        step_fail("CXL MPI SHIM is missing or unreadable on node0: %s" % SHIM, output)


def osu_command(osu_bin=OSU_BIN):
    return (
        "unset CXL_SHIM_VERBOSE CXL_SHIM_TRACE; "
        "export CXL_DAX_PATH=/dev/dax0.0 CXL_DAX_RESET=1; "
        "LD_PRELOAD=%s mpirun --allow-run-as-root -np 2 -hostfile %s "
        "--mca plm_rsh_args '-o PreferredAuthentications=password -o PubkeyAuthentication=no "
        "-o PasswordAuthentication=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' "
        "-x CXL_DAX_PATH -x CXL_DAX_RESET -x LD_PRELOAD %s -m 2:16384; echo OSU_RC=$?" % (SHIM, HOSTFILE, osu_bin)
    )


def peer_password_ssh_command():
    return (
        "ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no "
        "-o PasswordAuthentication=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=5 root@node1 hostname"
    )


def ensure_hostfile(node0):
    """Create and verify the MPI hostfile through the candidate serial console."""
    entries = ["%s slots=1" % hostname for _, hostname, _, _, _ in NODES]
    add_entries = ["grep -qx '%s' %s || echo '%s' >> %s" % (entry, HOSTFILE, entry, HOSTFILE)
                   for entry in entries]
    predicate = "test -f %s && %s" % (HOSTFILE, " && ".join(
        "grep -qx '%s' %s" % (entry, HOSTFILE) for entry in entries))
    run_guest(node0, "touch %s; %s" % (HOSTFILE, "; ".join(add_entries)))
    validation = run_guest(node0, "%s && echo ready || echo missing" % predicate)
    if serial_console_text(validation).strip().splitlines()[-1:] != ["ready"]:
        step_fail("could not create the two-node MPI hostfile", validation)
    step_pass("node0 hostfile contains node0 and node1")


def run_guest_password(vm, command, timeout=60):
    """Run a guest command, answering any root-password SSH prompts on serial."""
    vm.sendline(command)
    output = []
    while True:
        result = vm.expect([r"[Pp]assword:", PROMPT], timeout=timeout)
        output.append(vm.before)
        if result == 1:
            return serial_console_text("".join(output))
        vm.sendline(ROOT_PASSWORD)


def devdax_ready(raw_status, capacity_mib):
    """Return whether daxctl reports a suitably sized /dev/dax0.0 devdax device."""
    cleaned_status = serial_console_text(raw_status)
    json_start = cleaned_status.find("[")
    json_end = cleaned_status.rfind("]")
    if json_start < 0 or json_end < json_start:
        return False
    try:
        devices = json.loads(cleaned_status[json_start:json_end + 1])
    except json.JSONDecodeError:
        return False
    if isinstance(devices, dict):
        devices = [devices]
    expected_size = capacity_mib * 1024 * 1024
    for device in devices:
        if not isinstance(device, dict) or device.get("chardev", device.get("dev")) != "dax0.0":
            continue
        try:
            size = int(device.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        return device.get("mode") == "devdax" and size >= expected_size * 9 // 10
    return False


def dax_device_ready(raw_marker):
    return serial_console_text(raw_marker).strip().endswith("dax-ready")


def dax_provisioning_command():
    """List transient namespace provisioners, excluding the monitor daemon."""
    return (
        "ps -eo args= | awk "
        "'$0 ~ /(^|[[:space:]\\/])(ndctl|daxctl)([[:space:]]|$)/ && "
        "$0 !~ /(^|[[:space:]\\/])ndctl-monitor([[:space:]]|$)/ { print }' || true"
    )


def dax_host_list_command():
    return "daxctl list -H 2>&1 || true"


def probe_dax_wait_ssh(pending):
    """Report password-SSH liveness without turning a slow DAX setup into an SSH failure."""
    for _, (_, hostname, address, _, _) in pending:
        try:
            ensure_host_password_ssh(hostname, address)
        except (StepError, pexpect.ExceptionPexpect, OSError) as error:
            warn("%s password SSH is unavailable during DAX provisioning: %s" % (hostname, error))


def wait_for_dax_group(vms, capacity_mib, timeout, ssh_probe_after=DAX_SSH_PROBE_AFTER):
    """Wait for both guests using the integration runner's grouped DAX protocol."""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    last_report = None
    ssh_probed = False
    pending = list(zip(vms, NODES))
    while pending and time.monotonic() < deadline:
        provisioners = {name: serial_console_text(run_guest(vm, dax_provisioning_command())).strip()
                        for vm, (name, _, _, _, _) in pending}
        now = time.monotonic()
        ready = []
        waiting = []
        for vm, node in pending:
            name = node[0]
            if not provisioners[name]:
                run_guest(vm, dax_host_list_command())
            device = run_guest(vm, "test -c /dev/dax0.0 && echo dax-ready || true")
            status = run_guest(vm, "daxctl list -D 2>/dev/null || true")
            if dax_device_ready(device) and devdax_ready(status, capacity_mib):
                ready.append((vm, node))
            else:
                waiting.append((vm, node))
        for _, (name, _, _, _, _) in ready:
            if provisioners[name]:
                warn("%s /dev/dax0.0 is ready while namespace provisioning remains active" % name)
            step_pass("%s /dev/dax0.0 is initialized (devdax)" % name)
        pending = waiting
        if not pending:
            return
        if any(provisioners.values()):
            if not ssh_probed and now - started >= ssh_probe_after:
                info("DAX provisioning exceeded %d seconds; checking password SSH" % ssh_probe_after)
                probe_dax_wait_ssh(pending)
                ssh_probed = True
            if last_report is None or now - last_report >= DAX_PROGRESS_INTERVAL:
                for _, (name, _, _, _, _) in pending:
                    activity = "Provisioning /dev/dax0.0" if provisioners[name] else "Waiting for peer provisioning"
                    info(dax_progress_line(now - started, name, activity))
                last_report = now
            time.sleep(DAX_POLL_INTERVAL)
            continue
        if pending and (last_report is None or now - last_report >= DAX_PROGRESS_INTERVAL):
            for _, (name, _, _, _, _) in pending:
                info(dax_progress_line(now - started, name, "Waiting for /dev/dax0.0 (devdax unavailable)"))
            last_report = now
        if pending:
            time.sleep(DAX_POLL_INTERVAL)
    if pending:
        vm, (name, _, _, _, _) = pending[0]
        detail = run_guest(vm, "systemctl status --no-pager cxl-numa-setup.service 2>&1 || true")
        step_fail("%s did not expose usable /dev/dax0.0 devdax within %d seconds" % (name, timeout), detail)


def setup_peer_network(node0, node1):
    for vm in (node0, node1):
        run_guest(vm, "grep -qx '192.168.100.10 node0' /etc/hosts || echo '192.168.100.10 node0' >> /etc/hosts; "
                      "grep -qx '192.168.100.11 node1' /etc/hosts || echo '192.168.100.11 node1' >> /etc/hosts")


def ensure_peer_password_ssh(node0):
    output = run_guest_password(node0, peer_password_ssh_command(), timeout=30)
    if output.strip().splitlines()[-1:] != ["node1"]:
        step_fail("node0 cannot reach node1 through password SSH", output)
    step_pass("node0 reaches node1 through password SSH")


def sync_shim(vms, shim):
    for vm, (_, _, address, _, _) in zip(vms, NODES):
        scp_to_guest(str(shim), SHIM, address, "candidate %s" % address)
        run_guest(vm, "chmod 755 %s" % SHIM)
    step_pass("copied the GROMACS MPI CXL shim to both candidate overlays")


def run_osu(node0):
    osu_bin = find_osu(node0)
    verify_shim(node0)
    output = run_guest_password(node0, osu_command(osu_bin), timeout=660)
    print(output, flush=True)
    return_codes = re.findall(r"OSU_RC=(\d+)", output)
    if not return_codes or return_codes[-1] != "0":
        step_fail("OSU allgather did not complete successfully (rc=%s)" %
                  (return_codes[-1] if return_codes else "unknown"), output)
    if "Avg Latency" not in output:
        step_fail("OSU allgather completed without its latency table", output)
    step_pass("OSU allgather completed")


def terminate(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def shutdown(vm):
    if vm and vm.isalive():
        try:
            vm.sendline("shutdown -h now || poweroff -f || true")
            vm.expect(pexpect.EOF, timeout=15)
        except (pexpect.ExceptionPexpect, OSError):
            pass
    if vm:
        vm.close(force=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=pathlib.Path, default=HERE / "disk-image/qemu.img")
    parser.add_argument("--kernel", type=pathlib.Path, default=HERE / "disk-image/bzImage")
    parser.add_argument("--qemu-binary", default=str(REPO_ROOT / "lib/qemu/build/qemu-system-x86_64"))
    parser.add_argument("--qemu-img", default=str(REPO_ROOT / "lib/qemu/build/qemu-img"))
    parser.add_argument("--qemu-data-dir", default=str(REPO_ROOT / "lib/qemu/pc-bios"))
    parser.add_argument("--server", default=str(REPO_ROOT / "build/cxlmemsim_server"))
    parser.add_argument("--shim", type=pathlib.Path, default=default_shim_path())
    parser.add_argument("--capacity", type=int, default=1024)
    parser.add_argument("--latency", type=int, default=70)
    parser.add_argument("--server-port", type=int, default=19124)
    parser.add_argument("--boot-timeout", type=int, default=300)
    parser.add_argument("--dax-timeout", type=int, default=1800)
    parser.add_argument("--dax-ssh-probe-after", type=int, default=DAX_SSH_PROBE_AFTER,
                        help="probe password SSH once after this many seconds of DAX provisioning")
    args = parser.parse_args()

    base_image, kernel, shim = args.image.resolve(), args.kernel.resolve(), args.shim.resolve()
    for path, label in ((base_image, "candidate image"), (kernel, "candidate kernel"),
                        (pathlib.Path(args.qemu_binary), "QEMU binary"), (pathlib.Path(args.qemu_img), "qemu-img"),
                        (pathlib.Path(args.server), "CXL simulator"), (shim, "GROMACS MPI shim")):
        require_file(path, label)
    if os.geteuid() != 0:
        raise SystemExit("run as root so QEMU can use tap and KVM")
    if args.capacity <= 0:
        raise SystemExit("--capacity must be positive")
    if args.dax_ssh_probe_after < 0:
        raise SystemExit("--dax-ssh-probe-after must be non-negative")

    state = HERE / "osu-allgather-state"
    state.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    overlays = [(HERE / "disk-image") / ("qemu-node%d.qcow2" % index) for index in range(2)]
    for overlay in overlays:
        create_overlay(args.qemu_img, base_image, overlay)
    cxl_memory = [state / ("cxl-memory-node%d.raw" % index) for index in range(2)]
    lsa_backing = state / "cxl-lsa.raw"
    for path in (*cxl_memory, lsa_backing):
        path.unlink(missing_ok=True)

    server = None
    vms = []
    console_logs = []
    server_log = state / ("server-%s.log" % stamp)
    try:
        with server_log.open("wb") as log:
            server = subprocess.Popen(
                server_command(args.server, args.capacity, args.latency, lsa_backing, args.server_port,
                               "/ocean-packer-osu-%d" % os.getpid()),
                cwd=REPO_ROOT / "build", stdout=log, stderr=subprocess.STDOUT,
            )
            time.sleep(2)
            if server.poll() is not None:
                raise RuntimeError("CXL simulator exited; see %s" % server_log)
            step_pass("candidate CXL simulator started")

            def launch_vm(index, node):
                _, hostname, _, tap, mac = node
                console_log = state / ("console-%s-%s.log" % (hostname, stamp))
                console_logs.append(console_log)
                log_handle = console_log.open("w", encoding="utf-8")
                command = qemu_command(args.qemu_binary, args.qemu_data_dir, kernel, overlays[index], args.capacity,
                                       cxl_memory[index], lsa_backing, tap, mac, hostname)
                environment = qemu_environment(os.environ, args.server_port, index, args.capacity)
                vm = pexpect.spawn(command[0], command[1:], cwd=HERE, encoding="utf-8", codec_errors="replace",
                                  timeout=args.boot_timeout, env=environment)
                vm.logfile_read = log_handle
                return vm

            def authenticate_vm(vm, node):
                name = node[0]
                login(vm, args.boot_timeout)
                step_pass("%s booted and logged in" % name)
                verify_ssh_policy(vm)

            launch_all_then_login(NODES, launch_vm, authenticate_vm, vms)
            wait_for_dax_group(vms, args.capacity, args.dax_timeout, args.dax_ssh_probe_after)
            ensure_osu(vms)
            ensure_host_password_ssh("node0", NODES[0][2])
            ensure_host_password_ssh("node1", NODES[1][2])
            sync_shim(vms, shim)
            setup_peer_network(vms[0], vms[1])
            ensure_hostfile(vms[0])
            ensure_peer_password_ssh(vms[0])
            run_osu(vms[0])
        print("Candidate-image OSU allgather completed successfully.", flush=True)
        return 0
    except (StepError, pexpect.ExceptionPexpect, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("[ FAIL ] %s" % error, file=sys.stderr, flush=True)
        print("Logs: %s; %s" % (server_log, ", ".join(map(str, console_logs))), file=sys.stderr, flush=True)
        return 1
    finally:
        for vm in reversed(vms):
            shutdown(vm)
        terminate(server)


if __name__ == "__main__":
    raise SystemExit(main())
