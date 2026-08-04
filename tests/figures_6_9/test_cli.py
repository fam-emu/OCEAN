from pathlib import Path

from figures_6_9.cli import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_UNAVAILABLE,
    build_parser,
    main,
)


def test_parser_exposes_five_commands():
    help_text = build_parser().format_help()
    for command in ("doctor", "collect", "validate", "plot", "all"):
        assert command in help_text


def test_doctor_reports_missing_tigon_without_mutation(
    repo_root: Path, tmp_path: Path, capsys
):
    config = repo_root / "script/figures_6_9/config.example.toml"
    before = set(tmp_path.iterdir())

    rc = main(["doctor", "--fig", "6", "--config", str(config)])

    assert rc == EXIT_UNAVAILABLE
    assert "fig6.workdir" in capsys.readouterr().err
    assert set(tmp_path.iterdir()) == before


def test_validate_and_plot_only_succeed_from_synthetic_fixture(
    normalized_fixture: Path, tmp_path: Path
):
    assert main(
        ["validate", "--fig", "all", "--input", str(normalized_fixture)]
    ) == EXIT_OK
    output = tmp_path / "plots"

    rc = main(
        [
            "plot", "--fig", "all", "--input", str(normalized_fixture),
            "--output", str(output), "--format", "png",
        ]
    )

    assert rc == EXIT_OK
    assert {path.name for path in output.iterdir()} == {
        "fig6.png", "fig7.png", "fig8.png", "fig9a.png", "fig9b.png", "fig9c.png"
    }


def test_plot_accepts_paper_style_error_bar_toggle(
    normalized_fixture: Path, tmp_path: Path
):
    rc = main(
        [
            "plot", "--fig", "6", "--input", str(normalized_fixture),
            "--output", str(tmp_path / "plots"), "--format", "png",
            "--show-error-bars",
        ]
    )

    assert rc == EXIT_OK
    assert (tmp_path / "plots/fig6.png").is_file()


def test_validate_returns_stable_invalid_exit_for_missing_table(
    tmp_path: Path, capsys
):
    rc = main(["validate", "--fig", "6", "--input", str(tmp_path)])

    assert rc == EXIT_INVALID
    assert "cannot read fig6 table" in capsys.readouterr().err
