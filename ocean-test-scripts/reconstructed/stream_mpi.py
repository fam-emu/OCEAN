from __future__ import annotations

import os
import sys

from .common import ROOT, create_overlay, load_file


def main(argv=None):
    sys.path.insert(0, str(ROOT / "qemu_integration"))
    runner = load_file("legacy_stream_candidate", ROOT / "qemu_integration/run_stream_mpi.py")
    runner.REPO_ROOT = str(ROOT)
    runner.BUILD_DIR = str(ROOT / "build")
    runner.QEMU_BINARY = str(ROOT / "lib/qemu/build/qemu-system-x86_64")
    base = ROOT / "artifact/ocean-qemu-image/disk-image/qemu.img"
    state = ROOT / "ocean-test-scripts/state"
    overlays = (create_overlay(base, state / "stream-mpi-node0.qcow2"), create_overlay(base, state / "stream-mpi-node1.qcow2"))
    os.environ["CXL_CANDIDATE_IMAGE_VM0"] = str(overlays[0])
    os.environ["CXL_CANDIDATE_IMAGE_VM1"] = str(overlays[1])
    runner.LAUNCHERS = (
        ("VM0", str(ROOT / "ocean-test-scripts/reconstructed/launch_vm0.sh"), "stream_mpi_vm0.log", "192.168.100.10"),
        ("VM1", str(ROOT / "ocean-test-scripts/reconstructed/launch_vm1.sh"), "stream_mpi_vm1.log", "192.168.100.11"),
    )
    runner.preflight = lambda capacity: _preflight(runner, capacity)
    old_argv = sys.argv
    sys.argv = ["run_stream_mpi.py", *(argv or [])]
    try:
        return int(runner.main())
    finally:
        sys.argv = old_argv
        for overlay in overlays:
            overlay.unlink(missing_ok=True)


def _preflight(runner, capacity):
    runner.preflight_cxl_test(
        capacity,
        [runner.QEMU_BINARY, str(ROOT / "artifact/ocean-qemu-image/disk-image/qemu.img"), str(ROOT / "artifact/ocean-qemu-image/disk-image/bzImage"), runner.STREAM_SOURCE],
        [(runner.BUILD_DIR, "cxlmemsim_server")],
        "preflight complete (capacity=%d MiB; reconstructed image ready)",
    )
