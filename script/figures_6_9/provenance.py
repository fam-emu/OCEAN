from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import socket
import subprocess
import sys

from .execution import RunResult


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo_root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        shell=False,
    )
    return process.stdout.strip()


def build_manifest(
    repo_root: Path,
    configuration_bytes: bytes,
    source: str,
    runs: list[RunResult],
    produced_files: list[Path],
) -> dict[str, object]:
    revision = _git(repo_root, "rev-parse", "HEAD")
    dirty = bool(_git(repo_root, "status", "--porcelain"))
    commands = []
    for run in runs:
        command = asdict(run)
        command["argv"] = list(run.argv)
        command.pop("output")
        commands.append(command)
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision,
        "git_dirty": dirty,
        "source": source,
        "configuration_sha256": hashlib.sha256(configuration_bytes).hexdigest(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "commands": commands,
        "files": {str(path): sha256_file(path) for path in produced_files},
    }


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
