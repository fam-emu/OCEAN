import hashlib
import json
from pathlib import Path

from figures_6_9.execution import RunResult
from figures_6_9.provenance import build_manifest, sha256_file, write_manifest


def test_sha256_file_matches_hashlib(tmp_path: Path):
    path = tmp_path / "data.csv"
    path.write_bytes(b"ocean\n")

    assert sha256_file(path) == hashlib.sha256(b"ocean\n").hexdigest()


def test_manifest_records_revision_commands_and_outputs(repo_root: Path, tmp_path: Path):
    output = tmp_path / "fig6.csv"
    output.write_text("coverage_pct\n0\n", encoding="utf-8")
    run = RunResult(
        argv=("runner", "--coverage", "0"),
        returncode=0,
        output="ok\n",
        started_utc="2026-08-04T00:00:00+00:00",
        ended_utc="2026-08-04T00:00:01+00:00",
        dry_run=False,
    )

    manifest = build_manifest(
        repo_root,
        b"[run]\nsource='measured'\n",
        "measured",
        [run],
        [output],
    )

    assert len(manifest["git_revision"]) == 40
    assert isinstance(manifest["git_dirty"], bool)
    assert manifest["source"] == "measured"
    assert manifest["commands"][0]["argv"] == ["runner", "--coverage", "0"]
    assert manifest["files"][str(output)] == sha256_file(output)
    assert len(manifest["configuration_sha256"]) == 64

    target = tmp_path / "manifest.json"
    write_manifest(target, manifest)
    assert json.loads(target.read_text()) == manifest
