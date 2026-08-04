from pathlib import Path
from string import Formatter
import tomllib

from .errors import ConfigError


def load_config(path: Path, repo_root: Path) -> dict:
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"cannot load configuration {path}: {error}") from error

    run = config.setdefault("run", {})
    output_root = Path(run.get("output_root", "artifact/figures_6_9"))
    run["output_root"] = (
        output_root if output_root.is_absolute() else repo_root / output_root
    )
    run.setdefault("repetitions", 3)
    run.setdefault("timeout_s", 3600)
    run.setdefault("source", "measured")
    return config


def expand_command(argv: list[str], values: dict[str, object]) -> list[str]:
    fields = {
        field
        for token in argv
        for _, field, _, _ in Formatter().parse(token)
        if field
    }
    unknown = fields - values.keys()
    if unknown:
        raise ConfigError(f"unknown placeholder: {sorted(unknown)[0]}")
    return [token.format_map(values) for token in argv]
