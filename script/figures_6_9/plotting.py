from collections import defaultdict
from pathlib import Path
from statistics import fmean, stdev
from typing import Callable, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .errors import ConfigError
from .schemas import read_rows
from .validation import validate_rows


FIG8_POLICY_ORDER = (
    "Baseline",
    "Interleave",
    "NUMA",
    "Frequency",
    "PageTableAware",
    "FIFO",
    "HeatAware",
    "Hybrid",
    "Locality",
    "CacheFrequency",
    "HugePage",
    "Lifetime",
    "LoadBalance",
)
FIG9_OPERATION_ORDER = ("os", "cas_raw", "cas_flush", "or", "full_rt")
FIG9_OPERATION_LABELS = {
    "os": r"$o_s$ (flush)",
    "cas_raw": "CAS",
    "cas_flush": "Flush + CAS",
    "or": r"$o_r$ (invalidate + load)",
    "full_rt": "Full round trip",
}
FIG9_SCENARIO_ORDER = ("Default", "Real HW", "OCEAN calibrated")

COLORS = ("#4472C4", "#ED7D31", "#70AD47", "#A5A5A5", "#7030A0")
MARKERS = ("o", "s", "^", "D", "v")


def _style() -> None:
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.6,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def _mean_error(values: Iterable[float]) -> tuple[float, float]:
    series = list(values)
    return fmean(series), stdev(series) if len(series) > 1 else 0.0


def _load(input_dir: Path, table: str, allow_mixed_sources: bool) -> list[dict[str, object]]:
    rows = read_rows(input_dir / f"{table}.csv", table)
    validate_rows(table, rows, allow_mixed=allow_mixed_sources)
    return rows


def _save(
    figure: plt.Figure, output_dir: Path, stem: str, formats: tuple[str, ...]
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for output_format in formats:
        if output_format not in {"pdf", "png"}:
            raise ConfigError(f"unsupported plot format: {output_format}")
        path = output_dir / f"{stem}.{output_format}"
        figure.savefig(
            path,
            format=output_format,
            dpi=300 if output_format == "png" else None,
            bbox_inches="tight",
        )
        outputs.append(path)
    plt.close(figure)
    return outputs


def _plot_fig6(rows: list[dict[str, object]], show_error_bars: bool) -> plt.Figure:
    by_node: dict[tuple[int, int], list[float]] = defaultdict(list)
    by_total: dict[tuple[int, int], float] = defaultdict(float)
    for row in rows:
        coverage = int(row["coverage_pct"])
        repetition = int(row["repetition"])
        throughput = float(row["throughput_txn_s"])
        by_node[(coverage, int(row["node_id"]))].append(throughput)
        by_total[(coverage, repetition)] += throughput
    coverages = sorted({key[0] for key in by_node})
    figure, axis = plt.subplots(figsize=(3.45, 2.45))
    series = []
    for node_id in (0, 1):
        series.append(
            (
                f"Node {node_id}",
                [_mean_error(by_node[(coverage, node_id)]) for coverage in coverages],
            )
        )
    series.append(
        (
            "Total",
            [
                _mean_error(
                    value
                    for (point, _), value in by_total.items()
                    if point == coverage
                )
                for coverage in coverages
            ],
        )
    )
    for index, (label, points) in enumerate(series):
        means = [point[0] for point in points]
        errors = [point[1] for point in points] if show_error_bars else None
        axis.errorbar(
            coverages,
            means,
            yerr=errors,
            label=label,
            color=COLORS[index],
            marker=MARKERS[index],
            linewidth=1.6,
            markersize=4,
            capsize=2,
        )
    axis.set_xlabel("Hardware cache-coherence coverage (%)")
    axis.set_ylabel("NewOrder throughput (txn/s)")
    axis.set_xticks(coverages)
    axis.legend(frameon=False, ncol=3, loc="upper left")
    return figure


def _plot_fig7(rows: list[dict[str, object]], show_error_bars: bool) -> plt.Figure:
    grouped: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["protocol"]), int(row["write_ratio_pct"]))].append(
            float(row["throughput_txn_s"])
        )
    ratios = list(range(0, 101, 10))
    figure, axis = plt.subplots(figsize=(3.45, 2.45))
    for index, protocol in enumerate(("Tigon", "DS2PL+", "Sundial+")):
        points = [_mean_error(grouped[(protocol, ratio)]) for ratio in ratios]
        axis.errorbar(
            ratios,
            [point[0] for point in points],
            yerr=[point[1] for point in points] if show_error_bars else None,
            label=protocol,
            color=COLORS[index],
            marker=MARKERS[index],
            linewidth=1.6,
            markersize=4,
            capsize=2,
        )
    axis.set_xlabel("Write ratio (%)")
    axis.set_ylabel("YCSB throughput (txn/s)")
    axis.set_xlim(0, 100)
    axis.set_xticks(range(0, 101, 20))
    axis.legend(frameon=False)
    return figure


def _plot_fig8(rows: list[dict[str, object]], show_error_bars: bool) -> plt.Figure:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["backend"]), str(row["policy"]))].append(
            float(row["elapsed_s"])
        )
    x = np.arange(len(FIG8_POLICY_ORDER))
    width = 0.38
    figure, axis = plt.subplots(figsize=(7.15, 2.75))
    for index, backend in enumerate(("SHM", "TCP")):
        points = [_mean_error(grouped[(backend, policy)]) for policy in FIG8_POLICY_ORDER]
        axis.bar(
            x + (index - 0.5) * width,
            [point[0] for point in points],
            width,
            yerr=[point[1] for point in points] if show_error_bars else None,
            label=backend,
            color=COLORS[index],
            edgecolor="black",
            linewidth=0.35,
            capsize=2,
        )
    axis.set_ylabel("PEPSIN execution time (s)")
    axis.set_xticks(x, FIG8_POLICY_ORDER, rotation=35, ha="right")
    axis.legend(frameon=False, ncol=2)
    return figure


def _plot_fig9a(rows: list[dict[str, object]]) -> plt.Figure:
    by_operation: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_operation[str(row["operation"])].append(float(row["latency_ns"]))
    figure, axis = plt.subplots(figsize=(3.45, 2.45))
    for index, operation in enumerate(FIG9_OPERATION_ORDER):
        values = np.sort(by_operation[operation])
        cdf = np.arange(1, len(values) + 1) / len(values)
        axis.plot(
            values,
            cdf,
            label=FIG9_OPERATION_LABELS[operation],
            color=COLORS[index],
            linewidth=1.5,
        )
    axis.set_xlabel("Latency (ns)")
    axis.set_ylabel("CDF")
    axis.set_ylim(0, 1.01)
    axis.legend(frameon=False, loc="lower right")
    return figure


def _plot_fig9b(rows: list[dict[str, object]]) -> plt.Figure:
    by_scenario = {str(row["scenario"]): row for row in rows}
    x = np.arange(len(FIG9_SCENARIO_ORDER))
    figure, axis = plt.subplots(figsize=(3.45, 2.45))
    bottom = np.zeros(len(x))
    for index, (field, label) in enumerate(
        (("o_s_ns", r"$o_s$"), ("L_ns", r"$L$"), ("o_r_ns", r"$o_r$"))
    ):
        values = np.array([float(by_scenario[name][field]) for name in FIG9_SCENARIO_ORDER])
        axis.bar(
            x,
            values,
            bottom=bottom,
            label=label,
            color=COLORS[index],
            edgecolor="black",
            linewidth=0.35,
        )
        bottom += values
    for index, scenario in enumerate(FIG9_SCENARIO_ORDER):
        row = by_scenario[scenario]
        axis.annotate(
            f"g={float(row['g_ns']):.1f} ns\n{float(row['bandwidth_gbps']):.1f} Gb/s",
            (index, bottom[index]),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    axis.set_ylabel("LogP latency components (ns)")
    axis.set_xticks(x, FIG9_SCENARIO_ORDER, rotation=15, ha="right")
    axis.legend(
        frameon=False,
        ncol=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.18),
    )
    axis.margins(y=0.22)
    return figure


def _plot_fig9c(rows: list[dict[str, object]]) -> plt.Figure:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["series"])].append(
            (float(row["effective_utilization"]), float(row["added_latency_ns"]))
        )
    figure, axis = plt.subplots(figsize=(3.45, 2.45))
    order = ("OCEAN default", "OCEAN calibrated", "Measured (real HW)")
    for index, name in enumerate(order):
        points = sorted(grouped[name])
        axis.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            label=name,
            color=COLORS[index],
            marker=MARKERS[index] if name.startswith("Measured") else None,
            linewidth=1.6,
            markersize=4,
        )
    defaults = dict(grouped["OCEAN default"])
    calibrated = dict(grouped["OCEAN calibrated"])
    shared = sorted(defaults.keys() & calibrated.keys())
    axis.fill_between(
        shared,
        [defaults[value] for value in shared],
        [calibrated[value] for value in shared],
        color="#B4C6E7",
        alpha=0.25,
        linewidth=0,
    )
    axis.set_xlabel("Effective utilization")
    axis.set_ylabel("Added latency (ns)")
    axis.set_xlim(0, 1)
    axis.legend(frameon=False)
    return figure


def plot_all(
    input_dir: Path,
    output_dir: Path,
    formats: tuple[str, ...] = ("pdf", "png"),
    show_error_bars: bool = False,
    allow_mixed_sources: bool = False,
    figures: tuple[str, ...] = ("6", "7", "8", "9"),
) -> list[Path]:
    _style()
    if not formats:
        raise ConfigError("at least one plot format is required")
    requested = set(figures)
    unknown = requested - {"6", "7", "8", "9"}
    if unknown:
        raise ConfigError(f"unknown figures: {sorted(unknown)}")
    outputs: list[Path] = []
    jobs: list[tuple[str, str, Callable[[list[dict[str, object]]], plt.Figure]]] = []
    if "6" in requested:
        jobs.append(("fig6", "fig6", lambda rows: _plot_fig6(rows, show_error_bars)))
    if "7" in requested:
        jobs.append(("fig7", "fig7", lambda rows: _plot_fig7(rows, show_error_bars)))
    if "8" in requested:
        jobs.append(("fig8", "fig8", lambda rows: _plot_fig8(rows, show_error_bars)))
    if "9" in requested:
        jobs.extend(
            (
                ("fig9_samples", "fig9a", _plot_fig9a),
                ("fig9_params", "fig9b", _plot_fig9b),
                ("fig9_contention", "fig9c", _plot_fig9c),
            )
        )
    for table, stem, builder in jobs:
        rows = _load(input_dir, table, allow_mixed_sources)
        outputs.extend(_save(builder(rows), output_dir, stem, tuple(dict.fromkeys(formats))))
    return outputs
