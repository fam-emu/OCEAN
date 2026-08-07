import argparse
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import shutil
import stat
import sys

from .collectors.fig6_tpcc import collect_tpcc
from .collectors.fig7_ycsb import collect_ycsb
from .collectors.fig8_gromacs import collect_gromacs
from .collectors.fig9_logp import collect_logp
from .config import load_config
from .errors import ConfigError, ReproductionError, UnavailableError, ValidationError
from .execution import RunResult
from .provenance import build_manifest, write_manifest
from .schemas import read_rows, write_rows
from .validation import validate_rows


EXIT_OK = 0
EXIT_UNAVAILABLE = 2
EXIT_INVALID = 3
EXIT_EXECUTION = 4

REPO_ROOT = Path(__file__).resolve().parents[2]
FIGURE_TABLES = {
    "6": ("fig6",),
    "7": ("fig7",),
    "8": ("fig8",),
    "9": ("fig9_samples", "fig9_params", "fig9_contention"),
}


def _selected(selection: str) -> tuple[str, ...]:
    return ("6", "7", "8", "9") if selection == "all" else (selection,)


def _tables(selection: str) -> tuple[str, ...]:
    return tuple(
        table for figure in _selected(selection) for table in FIGURE_TABLES[figure]
    )


def _add_fig(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fig", choices=("6", "7", "8", "9", "all"), default="all")


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "script/figures_6_9/config.example.toml",
    )


def _add_formats(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("pdf", "png"), action="append")
    parser.add_argument("--allow-mixed-sources", action="store_true")
    parser.add_argument("--show-error-bars", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect, validate, and plot OCEAN.pdf Figures 6-9"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check prerequisites without mutation")
    _add_fig(doctor)
    _add_config(doctor)

    collect = subparsers.add_parser("collect", help="run configured experiments")
    _add_fig(collect)
    _add_config(collect)
    collect.add_argument("--run-id")
    collect.add_argument("--dry-run", action="store_true")

    validate = subparsers.add_parser("validate", help="validate normalized CSV tables")
    _add_fig(validate)
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--allow-mixed-sources", action="store_true")

    plot = subparsers.add_parser("plot", help="plot existing normalized CSV tables")
    _add_fig(plot)
    plot.add_argument("--input", type=Path, required=True)
    plot.add_argument("--output", type=Path, required=True)
    _add_formats(plot)

    all_command = subparsers.add_parser(
        "all", help="doctor, collect, validate, and plot in sequence"
    )
    _add_fig(all_command)
    _add_config(all_command)
    all_command.add_argument("--run-id")
    all_command.add_argument("--dry-run", action="store_true")
    _add_formats(all_command)
    return parser


def _resolve_workdir(section_name: str, section: dict, problems: list[str]) -> Path | None:
    raw = Path(str(section.get("workdir", ".")))
    workdir = raw if raw.is_absolute() else REPO_ROOT / raw
    if not workdir.is_dir():
        problems.append(f"{section_name}.workdir does not exist: {workdir}")
        return None
    return workdir


def _check_executable(
    section_name: str, section: dict, workdir: Path, problems: list[str]
) -> None:
    command = section.get("command")
    if not isinstance(command, list) or not command or not isinstance(command[0], str):
        raise ConfigError(f"{section_name}.command must be a nonempty TOML array")
    executable = command[0]
    if "/" in executable:
        path = Path(executable)
        path = path if path.is_absolute() else workdir / path
        if not path.is_file() or not path.stat().st_mode & stat.S_IXUSR:
            problems.append(f"{section_name}.command executable is unavailable: {path}")
    elif shutil.which(executable) is None:
        problems.append(f"{section_name}.command executable is unavailable: {executable}")


def _doctor(config: dict, selection: str) -> None:
    problems = []
    for module in ("numpy", "matplotlib"):
        if importlib.util.find_spec(module) is None:
            problems.append(f"Python module is unavailable: {module}")
    for figure in _selected(selection):
        section_name = f"fig{figure}"
        section = config.get(section_name)
        if not isinstance(section, dict):
            raise ConfigError(f"missing configuration section [{section_name}]")
        workdir = _resolve_workdir(section_name, section, problems)
        if workdir is None:
            continue
        _check_executable(section_name, section, workdir, problems)
        if figure == "9":
            command = [str(token) for token in section.get("command", [])]
            if "--hostfile" in command:
                index = command.index("--hostfile") + 1
                if index >= len(command):
                    raise ConfigError("fig9.command has --hostfile without a path")
                hostfile = Path(command[index])
                hostfile = hostfile if hostfile.is_absolute() else workdir / hostfile
                if not hostfile.is_file():
                    problems.append(f"fig9 hostfile is unavailable: {hostfile}")
            benchmark_tokens = [
                token for token in command if token.endswith("cxl_switch_lock_bench_mpi")
            ]
            if not benchmark_tokens:
                raise ConfigError("fig9.command does not name cxl_switch_lock_bench_mpi")
            benchmark_token = benchmark_tokens[0].replace(
                "{repo_root}", str(REPO_ROOT)
            ).replace("{workdir}", str(workdir))
            benchmark = Path(benchmark_token)
            benchmark = benchmark if benchmark.is_absolute() else workdir / benchmark
            if not benchmark.is_file() or not benchmark.stat().st_mode & stat.S_IXUSR:
                problems.append(f"fig9 MPI benchmark is unavailable: {benchmark}")
            dax_scope = str(section.get("dax_scope", "host"))
            if dax_scope not in {"host", "runner"}:
                raise ConfigError("fig9.dax_scope must be 'host' or 'runner'")
            if dax_scope == "host":
                dax = Path(str(section.get("dax_path", "")))
                try:
                    dax_mode = dax.stat().st_mode
                except OSError:
                    problems.append(f"fig9 DAX device is unavailable: {dax}")
                else:
                    if not stat.S_ISCHR(dax_mode):
                        problems.append(f"fig9 DAX path is not a character device: {dax}")
    if problems:
        raise UnavailableError("; ".join(problems))


def _new_run_root(config: dict, run_id: str | None) -> Path:
    identifier = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not identifier or identifier in {".", ".."} or "/" in identifier:
        raise ConfigError("run-id must be one path component")
    root = Path(config["run"]["output_root"]) / identifier
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ConfigError(f"run output already exists: {root}") from error
    return root


def _collect(
    config: dict, selection: str, run_root: Path, dry_run: bool
) -> tuple[list[RunResult], list[Path]]:
    collectors = {
        "6": lambda: ({"fig6": collect_tpcc(config, REPO_ROOT, run_root, dry_run)}),
        "7": lambda: ({"fig7": collect_ycsb(config, REPO_ROOT, run_root, dry_run)}),
        "8": lambda: ({"fig8": collect_gromacs(config, REPO_ROOT, run_root, dry_run)}),
    }
    runs: list[RunResult] = []
    produced: list[Path] = []
    for figure in _selected(selection):
        if figure == "9":
            table_rows, figure_runs = collect_logp(config, REPO_ROOT, run_root, dry_run)
        else:
            packed = collectors[figure]()
            table, (rows, figure_runs) = next(iter(packed.items()))
            table_rows = {table: rows}
        runs.extend(figure_runs)
        if dry_run:
            continue
        for table, rows in table_rows.items():
            validate_rows(table, rows)
            path = run_root / "normalized" / f"{table}.csv"
            write_rows(path, table, rows)
            produced.append(path)
    return runs, produced


def _validate_directory(input_dir: Path, selection: str, allow_mixed: bool) -> list[Path]:
    paths = []
    for table in _tables(selection):
        path = input_dir / f"{table}.csv"
        rows = read_rows(path, table)
        validate_rows(table, rows, allow_mixed=allow_mixed)
        paths.append(path)
    return paths


def _formats(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(dict.fromkeys(args.format or ("pdf", "png")))


def command_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config, REPO_ROOT)
    _doctor(config, args.fig)
    print(f"ok: prerequisites available for figure selection {args.fig}")
    return EXIT_OK


def command_collect(args: argparse.Namespace) -> int:
    config = load_config(args.config, REPO_ROOT)
    run_root = _new_run_root(config, args.run_id)
    runs, produced = _collect(config, args.fig, run_root, args.dry_run)
    if args.dry_run:
        for run in runs:
            print("DRY RUN:", " ".join(run.argv))
    else:
        manifest = build_manifest(
            REPO_ROOT,
            args.config.read_bytes(),
            str(config["run"]["source"]),
            runs,
            produced,
        )
        write_manifest(run_root / "manifest.json", manifest)
    print(run_root)
    return EXIT_OK


def command_validate(args: argparse.Namespace) -> int:
    paths = _validate_directory(args.input, args.fig, args.allow_mixed_sources)
    print(f"ok: validated {len(paths)} table(s)")
    return EXIT_OK


def command_plot(args: argparse.Namespace) -> int:
    from .plotting import plot_all

    outputs = plot_all(
        args.input,
        args.output,
        formats=_formats(args),
        show_error_bars=args.show_error_bars,
        allow_mixed_sources=args.allow_mixed_sources,
        figures=_selected(args.fig),
    )
    for path in outputs:
        print(path)
    return EXIT_OK


def command_all(args: argparse.Namespace) -> int:
    from .plotting import plot_all

    config = load_config(args.config, REPO_ROOT)
    _doctor(config, args.fig)
    run_root = _new_run_root(config, args.run_id)
    runs, normalized = _collect(config, args.fig, run_root, args.dry_run)
    if args.dry_run:
        for run in runs:
            print("DRY RUN:", " ".join(run.argv))
        print(run_root)
        return EXIT_OK
    _validate_directory(run_root / "normalized", args.fig, False)
    plots = plot_all(
        run_root / "normalized",
        run_root / "plots",
        formats=_formats(args),
        show_error_bars=args.show_error_bars,
        allow_mixed_sources=args.allow_mixed_sources,
        figures=_selected(args.fig),
    )
    manifest = build_manifest(
        REPO_ROOT,
        args.config.read_bytes(),
        str(config["run"]["source"]),
        runs,
        [*normalized, *plots],
    )
    write_manifest(run_root / "manifest.json", manifest)
    print(run_root)
    return EXIT_OK


COMMANDS = {
    "doctor": command_doctor,
    "collect": command_collect,
    "validate": command_validate,
    "plot": command_plot,
    "all": command_all,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except UnavailableError as error:
        print(f"unavailable: {error}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except (ConfigError, ValidationError) as error:
        print(f"invalid: {error}", file=sys.stderr)
        return EXIT_INVALID
    except ReproductionError as error:
        print(f"execution failed: {error}", file=sys.stderr)
        return EXIT_EXECUTION
