from pathlib import Path

from figures_6_9.plotting import FIG8_POLICY_ORDER, plot_all


def test_plot_all_writes_six_pdf_and_png_pairs(
    normalized_fixture: Path, tmp_path: Path
):
    outputs = plot_all(
        normalized_fixture,
        tmp_path / "plots",
        formats=("pdf", "png"),
        show_error_bars=False,
    )

    assert {path.stem for path in outputs} == {
        "fig6", "fig7", "fig8", "fig9a", "fig9b", "fig9c"
    }
    assert len(outputs) == 12
    assert all(path.stat().st_size > 1000 for path in outputs)


def test_paper_policy_order_is_explicit():
    assert FIG8_POLICY_ORDER == (
        "Baseline", "Interleave", "NUMA", "Frequency", "PageTableAware",
        "FIFO", "HeatAware", "Hybrid", "Locality", "CacheFrequency",
        "HugePage", "Lifetime", "LoadBalance",
    )
