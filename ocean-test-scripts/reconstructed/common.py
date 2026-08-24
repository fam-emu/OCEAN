from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_file(name: str, path: pathlib.Path):
    image_dir = ROOT / "artifact/ocean-qemu-image"
    if str(image_dir) not in sys.path:
        sys.path.insert(0, str(image_dir))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke_file(name: str, path: pathlib.Path, argv: list[str]) -> int:
    module = load_file(name, path)
    old_argv = sys.argv
    sys.argv = [str(path), *argv]
    try:
        return int(module.main())
    finally:
        sys.argv = old_argv


def candidate_path(name: str) -> pathlib.Path:
    return ROOT / "artifact/ocean-qemu-image" / name


def create_overlay(base: pathlib.Path, overlay: pathlib.Path) -> pathlib.Path:
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.unlink(missing_ok=True)
    qemu_img = ROOT / "lib/qemu/build/qemu-img"
    subprocess.run([str(qemu_img), "create", "-f", "qcow2", "-F", "raw", "-b", str(base), str(overlay)], check=True)
    return overlay
