from __future__ import annotations

import os
import sys

from .common import ROOT, create_overlay, load_file


def main(argv=None):
    sys.path.insert(0, str(ROOT / "qemu_integration"))
    runner = load_file("legacy_lat_mem_rd_candidate", ROOT / "qemu_integration/run_lat_mem_rd.py")
    runner.REPO_ROOT = str(ROOT)
    runner.BUILD_DIR = str(ROOT / "build")
    runner.LAUNCHER = str(ROOT / "ocean-test-scripts/reconstructed/launch_vm.sh")
    runner.preflight = lambda capacity: _preflight(runner, capacity)
    base = ROOT / "artifact/ocean-qemu-image/disk-image/qemu.img"
    overlay = ROOT / "ocean-test-scripts/state/lat-mem-rd.qcow2"
    create_overlay(base, overlay)
    old = os.environ.get("CXL_CANDIDATE_IMAGE")
    os.environ["CXL_CANDIDATE_IMAGE"] = str(overlay)
    os.environ["CXL_CANDIDATE_IMAGE_FORMAT"] = "qcow2"
    try:
        old_argv = sys.argv
        sys.argv = ["run_lat_mem_rd.py", *(argv or [])]
        try:
            return int(runner.main())
        finally:
            sys.argv = old_argv
    finally:
        if old is None:
            os.environ.pop("CXL_CANDIDATE_IMAGE", None)
        else:
            os.environ["CXL_CANDIDATE_IMAGE"] = old
        overlay.unlink(missing_ok=True)


def _preflight(runner, capacity):
    runner.preflight_cxl_test(
        capacity,
        [runner.QEMU_BINARY, os.environ["CXL_CANDIDATE_IMAGE"], str(ROOT / "artifact/ocean-qemu-image/disk-image/bzImage"), runner.LAT_MEM_RD, runner.PRELOAD],
        [(runner.BUILD_DIR, "cxlmemsim_server")],
        "preflight complete (capacity=%d MiB; reconstructed image ready)",
    )
