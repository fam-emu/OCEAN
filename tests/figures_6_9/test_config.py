from pathlib import Path

import pytest

from figures_6_9.config import ConfigError, expand_command, load_config


def test_load_config_resolves_paths_from_repository(repo_root: Path, tmp_path: Path):
    config = tmp_path / "repro.toml"
    config.write_text('[run]\noutput_root="artifact/figures_6_9"\n', encoding="utf-8")

    loaded = load_config(config, repo_root)

    assert loaded["run"]["output_root"] == repo_root / "artifact/figures_6_9"


def test_expand_command_rejects_unknown_placeholder():
    with pytest.raises(ConfigError, match="unknown placeholder: missing"):
        expand_command(["runner", "{missing}"], {"coverage_pct": 25})


def test_example_config_loads(repo_root: Path):
    loaded = load_config(
        repo_root / "script/figures_6_9/config.example.toml", repo_root
    )

    assert loaded["fig6"]["coverage_pct"] == [0, 25, 50, 70, 80, 90, 100]
    assert len(loaded["fig8"]["policies"]) == 13
