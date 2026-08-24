#!/usr/bin/env python3
"""Reusable boot-time readiness checks for CXL test VMs."""

import base64
import dataclasses
import hashlib
import json
import os
import re
import signal
import subprocess
import time

import pexpect


PROMPT = "OCEAN_CXL_PROMPT># "
RESET_MARKER = r"SeaBIOS \(version|[A-Za-z0-9._-]+ login: "
ROOT_PASSWORD = "victor129"
COMMAND_STATUS = "__OCEAN_COMMAND_STATUS="
ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


class StepError(Exception):
    """A test milestone failed."""


def status_line(label, message, color, use_color=None):
    if use_color is None:
        use_color = not os.environ.get("NO_COLOR")
    line = "[ %s ] %s" % (label, message)
    return "%s%s%s" % (color, line, RESET) if use_color else line


def step_pass(message):
    print(status_line("PASS", message, GREEN), flush=True)


def step_fail(message, detail=None):
    print(status_line("FAIL", message, RED), flush=True)
    if detail:
        print(detail[-2000:], flush=True)
    raise StepError(message)


def info(message):
    print("%s         %s%s" % (CYAN, message, RESET) if not os.environ.get("NO_COLOR") else "         %s" % message,
          flush=True)


def warn(message):
    print(status_line("WARN", message, YELLOW), flush=True)


def section(title):
    line = "========== %s ==========" % title
    print("%s%s%s" % (CYAN, line, RESET) if not os.environ.get("NO_COLOR") else line, flush=True)


def dax_progress_line(elapsed_seconds, vm_name, activity):
    """Return the compact, once-per-minute DAX readiness status line."""
    return "[t+%dm] %s: %s" % (int(elapsed_seconds // 60), vm_name, activity)


def start_cxlmemsim_server(build_dir, capacity, latency, log_name):
    """Start the simulator and return its process and open log handle."""
    log_path = os.path.join(build_dir, log_name)
    log = open(log_path, "wb")
    process = subprocess.Popen(
        ["./cxlmemsim_server", "--capacity=%d" % capacity, "--default_latency=%d" % latency],
        cwd=build_dir,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    time.sleep(2)
    if process.poll() is not None:
        log.close()
        step_fail("cxlmemsim_server exited immediately; see %s" % log_path)
    step_pass("cxlmemsim_server started (pid=%d)" % process.pid)
    return process, log


def stop_cxl_test_environment(vms, server, server_log):
    """Stop all launched VMs and the simulator, then remove named OCEAN state."""
    for vm in vms:
        try:
            vm.shutdown(timeout=5)
        except Exception as error:
            warn("could not stop %s: %s" % (vm.name, error))
    if server:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    if server_log:
        server_log.close()
    remove_ocean_shm_files()


def ocean_shm_files():
    return (
        os.environ.get("CXL_BACKING_FILE", "/dev/shm/cxl-kvm-direct.raw"),
        os.environ.get("CXL_LSA_FILE", "/dev/shm/lsa1.raw"),
        "/dev/shm/cxlmemsim_pgas",
    )


def remove_ocean_shm_files(paths=None):
    paths = tuple(ocean_shm_files() if paths is None else paths)
    for path in paths:
        try:
            os.unlink(path)
            info("removed %s" % path)
        except FileNotFoundError:
            pass
        except OSError as error:
            warn("could not remove %s: %s" % (path, error))
    return tuple(path for path in paths if os.path.lexists(path))


def expected_guest_identity(index):
    if index not in (0, 1):
        raise ValueError("only VM0 and VM1 are supported")
    return "node%d" % index, "192.168.100.%d/24" % (10 + index)


def parse_login_hostname(login_prompt):
    matches = re.findall(r"(?:^|\n)([A-Za-z0-9][A-Za-z0-9.-]*) login:", login_prompt.replace("\r", ""))
    return matches[-1] if matches else None


def clean_console_output(output):
    return ANSI_CSI.sub("", output.replace("\r", "")).strip()


def parse_systemctl_state(output, accepted_states):
    for line in reversed(clean_console_output(output).splitlines()):
        if line.strip() in accepted_states:
            return line.strip()
    return clean_console_output(output)


def ssh_repair_command():
    return (
        "mkdir -p /etc/ssh/sshd_config.d && "
        "printf 'PermitRootLogin yes\\nPasswordAuthentication yes\\n' > /etc/ssh/sshd_config.d/00-ocean-osu.conf && "
        "(chmod 600 /etc/ssh/ssh_host_*_key 2>/dev/null || true) && "
        "mkdir -p /run/sshd && chmod 755 /run/sshd && "
        "sshd -t && (systemctl restart ssh || systemctl restart sshd || service ssh restart)"
    )


def command_result(output):
    match = re.search(r"(?:^|\n)%s(\d+)\s*$" % COMMAND_STATUS, output)
    if not match:
        return output, None
    return output[:match.start()].rstrip(), int(match.group(1))


def sha256(path):
    """Return the SHA-256 checksum of a local artifact."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def guest_sha256(vm, path):
    """Return a guest artifact checksum, or None when it is absent."""
    output = vm.run("sha256sum %s 2>/dev/null || true" % path)
    match = re.search(r"\b([0-9a-f]{64})\b", output)
    return match.group(1) if match else None


def password_scp_arguments(source, destination, guest_ip):
    """Build the common password-authenticated SCP command arguments."""
    return [
        "-o", "PreferredAuthentications=password",
        "-o", "PubkeyAuthentication=no",
        "-o", "PasswordAuthentication=yes",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        source,
        "root@%s:%s" % (guest_ip, destination),
    ]


def scp_to_guest(source, destination, guest_ip, vm_name):
    """Copy an artifact to a guest using the CXL test image credentials."""
    child = pexpect.spawn(
        "scp",
        password_scp_arguments(source, destination, guest_ip),
        encoding="utf-8",
        codec_errors="replace",
        timeout=180,
    )
    try:
        result = child.expect([r"[Pp]assword:", pexpect.EOF], timeout=180)
        if result == 0:
            child.sendline(ROOT_PASSWORD)
            child.expect(pexpect.EOF, timeout=180)
    except pexpect.ExceptionPexpect as error:
        child.close(force=True)
        raise StepError("scp to %s failed: %s" % (vm_name, error)) from error
    output = child.before
    child.close()
    if child.exitstatus != 0:
        raise StepError("scp to %s failed for %s: %s" % (vm_name, source, output))


def hostfile_validation_command():
    return "grep -qx 'node0 slots=1' ~/hostfile && grep -qx 'node1 slots=1' ~/hostfile && echo ready || echo missing"


def ensure_hostfile(node0, check_write=False):
    command = "printf 'node0 slots=1\\nnode1 slots=1\\n' > ~/hostfile"
    if check_write:
        node0.run_checked(command)
    else:
        node0.run(command)
    if node0.run(hostfile_validation_command()).splitlines()[-1:] != ["ready"]:
        step_fail("could not create the two-node MPI hostfile", node0.recent())
    step_pass("node0 hostfile contains node0 and node1")


def password_ssh_arguments(address):
    return [
        "-o", "PreferredAuthentications=password",
        "-o", "PubkeyAuthentication=no",
        "-o", "PasswordAuthentication=yes",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "root@%s" % address,
        "hostname",
    ]


def ensure_host_password_ssh(expected_hostname, address):
    child = pexpect.spawn("ssh", password_ssh_arguments(address), encoding="utf-8", codec_errors="replace", timeout=30)
    try:
        result = child.expect([r"[Pp]assword:", pexpect.EOF], timeout=30)
        if result != 0:
            step_fail("host SSH to root@%s did not request a password: %s" % (address, child.before))
        child.sendline(ROOT_PASSWORD)
        child.expect(pexpect.EOF, timeout=30)
        output = child.before
    except pexpect.ExceptionPexpect as error:
        child.close(force=True)
        step_fail("host SSH to root@%s failed: %s" % (address, error))
    child.close()
    if child.exitstatus != 0 or output.strip().splitlines()[-1:] != [expected_hostname]:
        step_fail("host password SSH to root@%s failed: %s" % (address, output))
    step_pass("host password SSH reaches root@%s as %s without user keys" % (address, expected_hostname))


def ensure_peer_ssh(node0):
    output = node0.run("ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 node1 hostname 2>&1", timeout=30)
    if clean_console_output(output).splitlines()[-1:] != ["node1"]:
        step_fail("node0 cannot reach node1 by passwordless SSH: %s" % output, node0.recent())
    step_pass("node0 reaches node1 by passwordless SSH")


def reap_stale_cxl_processes():
    """Stop previous CXL test processes and remove their named shared state."""
    count = 0
    for pattern in ("qemu-system-x86_64 .*bzImage", "cxlmemsim_server"):
        try:
            output = subprocess.check_output(["pgrep", "-f", pattern], text=True, stderr=subprocess.DEVNULL)
            pids = [int(item) for item in output.split()]
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            pids = []
        for pid in pids:
            if pid != os.getpid():
                _kill_tree(pid, signal.SIGTERM)
                count += 1
    if count:
        time.sleep(2)
    remove_ocean_shm_files()
    step_pass("fresh CXL state prepared (%d stale processes reaped)" % count)


def preflight_cxl_test(capacity, required_paths, required_build_artifacts, success_message):
    """Validate host privileges, clean state, and runner-specific artifacts."""
    if os.geteuid() != 0:
        step_fail("run as root so QEMU can use tap and KVM")
    remaining_shm = remove_ocean_shm_files()
    if remaining_shm:
        step_fail("could not clear OCEAN shared memory: %s" % ", ".join(remaining_shm))
    step_pass("OCEAN shared-memory cleanup verified")
    if capacity <= 0:
        step_fail("capacity must be a positive number of MiB")
    for path in required_paths:
        if not os.path.exists(path):
            step_fail("required path is missing: %s" % path)
    for build_dir, artifact in required_build_artifacts:
        if not os.path.exists(os.path.join(build_dir, artifact)):
            step_fail("build/%s is missing" % artifact)
    step_pass(success_message % capacity)


def guest_contract_failures(expected, actual):
    failures = []
    for path, expected_sha in expected.items():
        actual_sha = actual.get(path)
        if actual_sha is None:
            failures.append("%s is missing" % path)
        elif actual_sha != expected_sha:
            failures.append("%s differs from qemu_integration" % path)
    return failures


@dataclasses.dataclass(frozen=True)
class DaxStatus:
    is_character_device: bool
    mode: str
    size_bytes: int

    @property
    def ready(self):
        return self.is_character_device and self.mode == "devdax" and self.size_bytes > 0

    def matches_expected_capacity(self, capacity_mb):
        if capacity_mb is None:
            return True
        expected_bytes = capacity_mb * 1024 * 1024
        return expected_bytes * 9 // 10 <= self.size_bytes <= expected_bytes


def parse_daxctl_status(raw, is_character_device):
    try:
        devices = json.loads(raw)
    except json.JSONDecodeError:
        return DaxStatus(is_character_device, "", 0)
    if isinstance(devices, dict):
        devices = [devices]
    for device in devices:
        if isinstance(device, dict) and device.get("chardev", device.get("dev")) == "dax0.0":
            try:
                size_bytes = int(device.get("size", 0))
            except (TypeError, ValueError):
                size_bytes = 0
            return DaxStatus(is_character_device, device.get("mode", ""), size_bytes)
    return DaxStatus(is_character_device, "", 0)


class _Tee:
    def __init__(self, file_handle):
        self.file_handle = file_handle

    def write(self, data):
        self.file_handle.write(data.encode("utf-8", "replace") if isinstance(data, str) else data)

    def flush(self):
        self.file_handle.flush()


class VM:
    """A serial-console connection to a VM launched by a QEMU helper."""

    def __init__(self, name, launch_script, env, log_path, mode, build_dir):
        self.name = name
        self.logfile = open(log_path, "wb")
        try:
            self.child = pexpect.spawn(
                "/bin/bash",
                ["-lc", "%s --%s" % (launch_script, mode)],
                cwd=build_dir,
                env=env,
                encoding="utf-8",
                codec_errors="replace",
                timeout=300,
                dimensions=(50, 200),
            )
        except Exception:
            self.logfile.close()
            raise
        self.child.logfile_read = _Tee(self.logfile)

    def recent(self):
        return clean_console_output(self.child.before or "")

    def wait_login(self, timeout):
        self.child.expect(r"(?:[A-Za-z0-9][A-Za-z0-9.-]* )?[Ll]ogin:", timeout=timeout)
        self.login_hostname = parse_login_hostname(self.child.after)

    def login(self, timeout=120):
        self.child.sendline("root")
        self.child.expect(r"[Pp]assword:", timeout=timeout)
        self.child.sendline(ROOT_PASSWORD)
        self.child.expect(r"# ", timeout=timeout)
        self.child.sendline("export PS1='%s'" % PROMPT)
        self.child.expect_exact(PROMPT)
        self.child.expect_exact(PROMPT)
        self.run("stty -echo 2>/dev/null; true")

    def run(self, command, timeout=60):
        self.child.sendline(command)
        result = self.child.expect([re.escape(PROMPT), RESET_MARKER], timeout=timeout)
        if result:
            step_fail("%s rebooted while running: %s" % (self.name, command[:80]), self.recent())
        lines = self.child.before.splitlines()
        if lines and command.strip() and lines[0].strip().endswith(command.strip()[-40:]):
            lines = lines[1:]
        return clean_console_output("\n".join(lines))

    def run_checked(self, command, timeout=60):
        output = self.run("%s; status=$?; printf '%s%%s\\n' \"$status\"" % (command, COMMAND_STATUS), timeout)
        output, status = command_result(output)
        if status != 0:
            step_fail("%s command failed (rc=%s): %s" % (self.name, status, command[:120]), self.recent())
        return output

    def shutdown(self, timeout=45):
        try:
            if self.child.isalive():
                try:
                    self.run("shutdown -h now 2>/dev/null || poweroff -f 2>/dev/null || true", timeout)
                    self.child.expect(pexpect.EOF, timeout=timeout)
                except (pexpect.TIMEOUT, pexpect.EOF, OSError, StepError):
                    pass
        finally:
            _kill_tree(self.child.pid, signal.SIGTERM)
            time.sleep(2)
            _kill_tree(self.child.pid, signal.SIGKILL)
            self.child.close(force=True)
            self.logfile.close()


def _kill_tree(pid, sig):
    try:
        output = subprocess.check_output(["pgrep", "-P", str(pid)], text=True, stderr=subprocess.DEVNULL)
        children = [int(item) for item in output.split()]
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        children = []
    for child in children:
        _kill_tree(child, sig)
    try:
        os.kill(pid, sig)
    except OSError:
        pass


class CxlVmFramework:
    """Validate and optionally repair the shared CXL guest boot contract."""

    def __init__(self, repo_root, repair_guest_config, expected_capacity_mb=None):
        integration_dir = os.path.join(repo_root, "qemu_integration")
        self.repair_guest_config = repair_guest_config
        self.expected_capacity_mb = expected_capacity_mb
        self.guest_files = {
            "/etc/systemd/system/cxl-numa-setup.service": os.path.join(integration_dir, "cxl-numa-setup.service"),
            "/usr/local/bin/fixed-numa-setup.sh": os.path.join(integration_dir, "fixed-numa-setup.sh"),
            "/usr/local/bin/enable-cxl-system-ram.sh": os.path.join(integration_dir, "enable-cxl-system-ram.sh"),
        }

    def validate_boot(self, vm, index, dax_timeout, dax_wait_hook=None):
        self.validate_pre_dax(vm, index, dax_timeout)
        self._wait_for_dax(vm, dax_timeout, dax_wait_hook)

    def validate_pre_dax(self, vm, index, restart_timeout):
        self._ensure_identity(vm, index)
        self._ensure_contract(vm, restart_timeout)
        self._ensure_ssh(vm)

    def _ensure_identity(self, vm, index):
        hostname, address = expected_guest_identity(index)
        actual_hostname = getattr(vm, "login_hostname", None)
        if actual_hostname == hostname:
            step_pass("%s booted image identifies itself as %s" % (vm.name, hostname))
            return
        if not self.repair_guest_config:
            step_fail("%s login banner identifies %s; expected %s (rerun with --repair-guest-config)"
                      % (vm.name, actual_hostname or "no hostname", hostname), vm.recent())
        vm.run_checked("hostnamectl set-hostname %s" % hostname)
        vm.run_checked("ip link set enp0s2 up && ip address flush dev enp0s2 && ip address add %s dev enp0s2 && "
                       "ip route replace default via 192.168.100.1" % address)
        repaired_hostname = vm.run("hostname").strip().splitlines()[-1]
        repaired_addresses = vm.run("ip -o -4 addr show dev enp0s2 2>/dev/null || true")
        if repaired_hostname != hostname or address not in repaired_addresses:
            step_fail("%s identity repair did not produce %s (%s)" % (vm.name, hostname, address), vm.recent())
        step_pass("%s runtime identity repaired to %s (%s); the next boot banner will verify it" %
                  (vm.name, hostname, address))

    def _expected_checksums(self):
        checksums = {}
        for guest, host in self.guest_files.items():
            with open(host, "rb") as file_handle:
                checksums[guest] = hashlib.sha256(file_handle.read()).hexdigest()
        return checksums

    @staticmethod
    def _guest_checksums(vm, paths):
        output = vm.run("sha256sum %s 2>/dev/null || true" % " ".join(paths))
        checksums = {}
        for line in output.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                checksums[parts[1].strip()] = parts[0]
        return checksums

    def _copy_to_guest(self, vm, source, destination):
        with open(source, "rb") as file_handle:
            source_bytes = file_handle.read()
        encoded = base64.b64encode(source_bytes).decode("ascii")
        expected_sha = hashlib.sha256(source_bytes).hexdigest()
        temporary = "/tmp/ocean-guest-config.b64"
        staged = "%s.ocean-new" % destination
        vm.run_checked("mkdir -p %s && : > %s" % (os.path.dirname(destination), temporary))
        for offset in range(0, len(encoded), 900):
            vm.run_checked("printf %%s '%s' >> %s" % (encoded[offset:offset + 900], temporary))
        vm.run_checked("base64 -d %s > %s && test \"$(sha256sum %s | awk '{print $1}')\" = %s && mv %s %s && rm -f %s"
                       % (temporary, staged, staged, expected_sha, staged, destination, temporary))
        if destination.endswith(".sh"):
            vm.run_checked("chmod 755 %s" % destination)

    def _ensure_contract(self, vm, restart_timeout):
        expected = self._expected_checksums()
        actual = self._guest_checksums(vm, expected)
        failures = guest_contract_failures(expected, actual)
        unit = vm.run("systemctl cat cxl-numa-setup.service 2>/dev/null || true")
        active = parse_systemctl_state(
            vm.run("systemctl is-active cxl-numa-setup.service 2>/dev/null || true"),
            {"active", "activating", "inactive", "failed"},
        )
        enabled = parse_systemctl_state(
            vm.run("systemctl is-enabled cxl-numa-setup.service 2>/dev/null || true"),
            {"enabled", "disabled", "static", "masked", "indirect"},
        )
        if "ExecStart=/usr/local/bin/fixed-numa-setup.sh" not in unit:
            failures.append("cxl-numa-setup.service has the wrong ExecStart")
        if active not in ("active", "activating"):
            failures.append("cxl-numa-setup.service is %s" % (active or "not installed"))
        if enabled != "enabled":
            failures.append("cxl-numa-setup.service is not enabled")
        if not failures:
            step_pass("%s CXL boot service and helpers match qemu_integration" % vm.name)
            return
        if not self.repair_guest_config:
            step_fail("%s CXL guest contract failed: %s (rerun with --repair-guest-config)"
                      % (vm.name, "; ".join(failures)), vm.recent())
        for destination, source in self.guest_files.items():
            if actual.get(destination) != expected[destination]:
                self._copy_to_guest(vm, source, destination)
        vm.run_checked("systemctl daemon-reload && systemctl enable cxl-numa-setup.service && "
                       "systemctl restart cxl-numa-setup.service", timeout=restart_timeout)
        repaired = self._guest_checksums(vm, expected)
        remaining = guest_contract_failures(expected, repaired)
        repaired_unit = vm.run("systemctl cat cxl-numa-setup.service 2>/dev/null || true")
        if "ExecStart=/usr/local/bin/fixed-numa-setup.sh" not in repaired_unit:
            remaining.append("cxl-numa-setup.service has the wrong ExecStart after repair")
        repaired_state = parse_systemctl_state(
            vm.run("systemctl is-active cxl-numa-setup.service 2>/dev/null || true"),
            {"active", "activating", "inactive", "failed"},
        )
        if repaired_state != "active":
            remaining.append("cxl-numa-setup.service is %s after repair" % (repaired_state or "unknown"))
        if parse_systemctl_state(
            vm.run("systemctl is-enabled cxl-numa-setup.service 2>/dev/null || true"),
            {"enabled", "disabled", "static", "masked", "indirect"},
        ) != "enabled":
            remaining.append("cxl-numa-setup.service is not enabled after repair")
        if remaining:
            step_fail("%s CXL guest contract repair failed: %s" % (vm.name, "; ".join(remaining)), vm.recent())
        step_pass("%s CXL boot service and helpers repaired" % vm.name)

    def _ensure_ssh(self, vm):
        effective = vm.run("sshd -T 2>/dev/null | grep -E '^(permitrootlogin|passwordauthentication) ' || true")
        expected = {"permitrootlogin yes", "passwordauthentication yes"}
        if expected.issubset(set(effective.lower().splitlines())):
            step_pass("%s SSH permits root password login" % vm.name)
            return
        if not self.repair_guest_config:
            step_fail("%s SSH does not permit root password login (rerun with --repair-guest-config)" % vm.name,
                      vm.recent())
        vm.run_checked(ssh_repair_command(), timeout=60)
        repaired = vm.run("sshd -T 2>/dev/null | grep -E '^(permitrootlogin|passwordauthentication) ' || true")
        if not expected.issubset(set(repaired.lower().splitlines())):
            step_fail("%s SSH repair did not enable root password login" % vm.name, vm.recent())
        step_pass("%s SSH root password login repaired" % vm.name)

    def _wait_for_dax(self, vm, timeout, wait_hook=None):
        deadline = time.time() + timeout
        started = time.time()
        last_report = None
        if wait_hook:
            wait_hook(vm)
        while time.time() < deadline:
            processes = self._dax_provisioning_processes(vm)
            if processes:
                if last_report is None or time.time() - last_report >= 60:
                    info(dax_progress_line(time.time() - started, vm.name, "Provisioning /dev/dax0.0"))
                    last_report = time.time()
                time.sleep(30)
                continue
            is_character_device = vm.run("test -c /dev/dax0.0 && echo yes || echo no").endswith("yes")
            raw = vm.run("daxctl list -D 2>&1 || true")
            status = parse_daxctl_status(raw, is_character_device)
            if status.ready and status.matches_expected_capacity(self.expected_capacity_mb):
                service_state = parse_systemctl_state(
                    vm.run("systemctl is-active cxl-numa-setup.service 2>/dev/null || true"),
                    {"active", "activating", "inactive", "failed"},
                )
                if service_state != "active":
                    step_fail("%s CXL boot service finished as %s" % (vm.name, service_state or "unknown"),
                              vm.run("systemctl status --no-pager cxl-numa-setup.service 2>&1 || true"))
                step_pass("%s /dev/dax0.0 is initialized (%d bytes, devdax)" % (vm.name, status.size_bytes))
                return
            if last_report is None or time.time() - last_report >= 60:
                info(dax_progress_line(
                    time.time() - started,
                    vm.name,
                    "Waiting for /dev/dax0.0 (mode=%s size=%d)" % (status.mode or "unknown", status.size_bytes),
                ))
                last_report = time.time()
            time.sleep(30)
        detail = vm.run("systemctl status --no-pager cxl-numa-setup.service 2>&1 || true")
        step_fail("%s /dev/dax0.0 was not initialized within %d seconds" % (vm.name, timeout), detail)

    @staticmethod
    def _dax_provisioning_processes(vm):
        """Return active ndctl/daxctl provisioning commands, not monitor daemons.

        ndctl-monitor.service is intentionally enabled in the guest and its
        long-lived command line contains "ndctl".  Treating it as a provisioner
        makes the DAX wait loop skip readiness checks indefinitely.
        """
        return vm.run(
            "ps -eo args= | awk "
            "'$0 ~ /(^|[[:space:]\\/])(ndctl|daxctl)([[:space:]]|$)/ && "
            "$0 !~ /(^|[[:space:]\\/])ndctl-monitor([[:space:]]|$)/ { print }' || true"
        )

    def wait_for_dax_group(self, vms, timeout):
        deadline = time.time() + timeout
        started = time.time()
        last_report = None
        pending = list(vms)
        while pending and time.time() < deadline:
            provisioning = {vm.name: self._dax_provisioning_processes(vm)
                            for vm in pending}
            if any(provisioning.values()):
                if last_report is None or time.time() - last_report >= 60:
                    for vm in pending:
                        state = "Provisioning /dev/dax0.0" if provisioning[vm.name] else "Waiting for peer provisioning"
                        info(dax_progress_line(time.time() - started, vm.name, state))
                    last_report = time.time()
                time.sleep(30)
                continue

            ready = []
            waiting = []
            for vm in pending:
                is_character_device = vm.run("test -c /dev/dax0.0 && echo yes || echo no").endswith("yes")
                raw = vm.run("daxctl list -D 2>&1 || true")
                status = parse_daxctl_status(raw, is_character_device)
                if status.ready and status.matches_expected_capacity(self.expected_capacity_mb):
                    service_state = parse_systemctl_state(
                        vm.run("systemctl is-active cxl-numa-setup.service 2>/dev/null || true"),
                        {"active", "activating", "inactive", "failed"},
                    )
                    if service_state != "active":
                        step_fail("%s CXL boot service finished as %s" % (vm.name, service_state or "unknown"),
                                  vm.run("systemctl status --no-pager cxl-numa-setup.service 2>&1 || true"))
                    ready.append((vm, status))
                else:
                    waiting.append((vm, status))
            for vm, status in ready:
                step_pass("%s /dev/dax0.0 is initialized (%d bytes, devdax)" % (vm.name, status.size_bytes))
                pending.remove(vm)
            if waiting and (last_report is None or time.time() - last_report >= 60):
                for vm, status in waiting:
                    info(dax_progress_line(
                        time.time() - started,
                        vm.name,
                        "Waiting for /dev/dax0.0 (mode=%s size=%d)" % (status.mode or "unknown", status.size_bytes),
                    ))
                last_report = time.time()
            if pending:
                time.sleep(30)
        if pending:
            detail = pending[0].run("systemctl status --no-pager cxl-numa-setup.service 2>&1 || true")
            step_fail("%s /dev/dax0.0 was not initialized within %d seconds" % (pending[0].name, timeout), detail)
