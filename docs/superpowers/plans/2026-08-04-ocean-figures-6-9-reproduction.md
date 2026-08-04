# OCEAN Figures 6–9 Reproduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed `collect -> validate -> plot` workflow for OCEAN paper Figures 6–9, including the missing optional MPI+DAX LogP calibration benchmark.

**Architecture:** A thin CLI dispatches to isolated collectors, which save raw logs and normalize them into six typed CSV tables. Validation and provenance are shared gates; Matplotlib plotters consume only validated tables. External Tigon, multi-host, and DAX requirements are configuration-driven and reported as unavailable rather than fabricated.

**Tech Stack:** Python 3.11+ standard library, pytest, Matplotlib, NumPy, C11, MPI C, CMake.

---

## File Map

- `script/reproduce_figures_6_9.py`: executable repository entrypoint.
- `script/figures_6_9/cli.py`: argument parsing and command orchestration.
- `script/figures_6_9/config.py`: TOML loading, defaults, and placeholder expansion.
- `script/figures_6_9/execution.py`: shell-free command execution and raw-log capture.
- `script/figures_6_9/provenance.py`: manifest metadata and checksums.
- `script/figures_6_9/schemas.py`: CSV schemas and typed read/write helpers.
- `script/figures_6_9/validation.py`: cross-row completeness and source-integrity rules.
- `script/figures_6_9/plotting.py`: Figures 6, 7, 8, and 9a–c rendering.
- `script/figures_6_9/collectors/*.py`: workload-specific planning and parsing.
- `script/figures_6_9/config.example.toml`: documented, non-privileged experiment configuration.
- `microbench/cxl_switch_lock_bench_mpi.c`: two-rank DAX timing benchmark with JSONL output.
- `microbench/CMakeLists.txt`: optional MPI benchmark target.
- `tests/figures_6_9/`: parser, validation, execution, plotting, and CLI tests.
- `docs/source/reproducing-figures-6-9.rst`: user workflow and proof boundaries.
- `docs/source/index.rst`: documentation navigation entry.

### Task 1: Package, configuration, and test harness

**Files:**
- Create: `script/figures_6_9/__init__.py`
- Create: `script/figures_6_9/errors.py`
- Create: `script/figures_6_9/config.py`
- Create: `script/figures_6_9/config.example.toml`
- Create: `tests/figures_6_9/conftest.py`
- Create: `tests/figures_6_9/test_config.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing configuration tests**

```python
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
    loaded = load_config(repo_root / "script/figures_6_9/config.example.toml", repo_root)
    assert loaded["fig6"]["coverage_pct"] == [0, 25, 50, 70, 80, 90, 100]
    assert len(loaded["fig8"]["policies"]) == 13
```

- [ ] **Step 2: Run the tests and confirm the import failure**

Run: `MPLCONFIGDIR=/tmp/ocean-mpl python3 -m pytest tests/figures_6_9/test_config.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'figures_6_9'`.

- [ ] **Step 3: Add package bootstrap and typed configuration errors**

```python
# script/figures_6_9/errors.py
class ReproductionError(RuntimeError):
    """Base error for the Figures 6-9 workflow."""


class ConfigError(ReproductionError):
    """Configuration is missing, malformed, or unsafe."""


class UnavailableError(ReproductionError):
    """A required external workload or device is unavailable."""


class ValidationError(ReproductionError):
    """Collected or supplied evidence failed an integrity gate."""
```

Add `tests/figures_6_9/conftest.py`:

```python
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "script"))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/ocean-mpl")


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
```

- [ ] **Step 4: Implement TOML loading and shell-free placeholder expansion**

```python
# script/figures_6_9/config.py
from pathlib import Path
from string import Formatter
import tomllib

from .errors import ConfigError


def load_config(path: Path, repo_root: Path) -> dict:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    run = config.setdefault("run", {})
    output_root = Path(run.get("output_root", "artifact/figures_6_9"))
    run["output_root"] = output_root if output_root.is_absolute() else repo_root / output_root
    run.setdefault("repetitions", 3)
    run.setdefault("timeout_s", 3600)
    run.setdefault("source", "measured")
    return config


def expand_command(argv: list[str], values: dict[str, object]) -> list[str]:
    fields = {
        field for token in argv for _, field, _, _ in Formatter().parse(token) if field
    }
    unknown = fields - values.keys()
    if unknown:
        raise ConfigError(f"unknown placeholder: {sorted(unknown)[0]}")
    return [token.format_map(values) for token in argv]
```

Create `config.example.toml` with `run`, `fig6`, `fig7`, `fig8`, and `fig9` sections. Commands must be TOML arrays, never shell strings. Set Figure 6 coverage to `[0,25,50,70,80,90,100]`, Figure 7 ratios to `0..100` by ten, Figure 8 backends to `SHM,TCP`, and its x-axis categories to the exact thirteen names in the design spec.

Use this complete configuration shape:

```toml
[run]
output_root = "artifact/figures_6_9"
repetitions = 3
timeout_s = 3600
source = "measured"

[fig6]
workdir = "workloads/tigon"
coverage_pct = [0, 25, 50, 70, 80, 90, 100]
command = ["./scripts/run_figure6.sh", "--coverage", "{coverage_pct}"]

[fig7]
workdir = "workloads/tigon"
protocols = ["Tigon", "DS2PL+", "Sundial+"]
write_ratio_pct = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
command = ["./scripts/run_figure7.sh", "--protocol", "{protocol}", "--write-ratio", "{write_ratio_pct}"]

[fig8]
workdir = "workloads/gromacs"
backends = ["SHM", "TCP"]
policies = ["Baseline", "Interleave", "NUMA", "Frequency", "PageTableAware", "FIFO", "HeatAware", "Hybrid", "Locality", "CacheFrequency", "HugePage", "Lifetime", "LoadBalance"]
command = ["./run_figure8.sh", "--backend", "{backend}", "--policy", "{policy}"]

[fig9]
workdir = "."
command = ["mpirun", "-np", "2", "--hostfile", "hostfile", "build/microbench/cxl_switch_lock_bench_mpi", "--dax", "{dax_path}", "--iterations", "{iterations}", "--map-offset", "{map_offset}", "--map-size", "{map_size}", "--acknowledge-dax-write"]
dax_path = "/dev/dax0.0"
iterations = 10000
map_offset = 0
map_size = 2097152
acknowledge_dax_writes = false
default_o_s_ns = 20.0
default_L_ns = 150.0
default_o_r_ns = 20.0
default_g_ns = 4.0
```

The example deliberately keeps `acknowledge_dax_writes = false`; Figure 9 collection must refuse to run until the operator explicitly acknowledges the exact configured DAX write range.

- [ ] **Step 5: Ignore generated evidence without hiding committed legacy artifacts**

Append to `.gitignore`:

```gitignore
artifact/figures_6_9/
script/figures_6_9/__pycache__/
tests/figures_6_9/__pycache__/
.pytest_cache/
```

- [ ] **Step 6: Run tests and commit**

Run: `MPLCONFIGDIR=/tmp/ocean-mpl python3 -m pytest tests/figures_6_9/test_config.py -q`

Expected: `3 passed`.

```bash
git add .gitignore script/figures_6_9 tests/figures_6_9
git commit -m "repro: add figures 6-9 configuration package"
```

### Task 2: Typed CSV schemas and validation gates

**Files:**
- Create: `script/figures_6_9/schemas.py`
- Create: `script/figures_6_9/validation.py`
- Create: `tests/figures_6_9/test_validation.py`

- [ ] **Step 1: Write failing schema and validation tests**

```python
import pytest

from figures_6_9.errors import ValidationError
from figures_6_9.schemas import coerce_rows
from figures_6_9.validation import validate_rows


def test_fig6_requires_every_coverage_and_two_nodes():
    rows = [
        {"coverage_pct": coverage, "node_id": node, "throughput_txn_s": 10_000.0,
         "repetition": 0, "source": "measured"}
        for coverage in [0, 25, 50, 70, 80, 90, 100] for node in [0, 1]
    ]
    validate_rows("fig6", rows)


def test_validation_rejects_mixed_source_types():
    rows = [
        {"coverage_pct": 0, "node_id": 0, "throughput_txn_s": 1.0,
         "repetition": 0, "source": "measured"},
        {"coverage_pct": 0, "node_id": 1, "throughput_txn_s": 1.0,
         "repetition": 0, "source": "synthetic"},
    ]
    with pytest.raises(ValidationError, match="mixed source types"):
        validate_rows("fig6", rows)


def test_coerce_rows_rejects_non_finite_metric():
    with pytest.raises(ValidationError, match="finite positive"):
        coerce_rows("fig8", [{"backend": "SHM", "policy": "Baseline",
                              "elapsed_s": "nan", "repetition": "0", "source": "measured"}])
```

- [ ] **Step 2: Confirm failures**

Run: `python3 -m pytest tests/figures_6_9/test_validation.py -q`

Expected: import errors for `schemas` and `validation`.

- [ ] **Step 3: Implement named schemas and CSV I/O**

Define `SCHEMAS` with the exact columns in the design spec and implement:

```python
def coerce_rows(table: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    spec = SCHEMAS[table]
    converted = []
    for row in rows:
        missing = set(spec) - row.keys()
        if missing:
            raise ValidationError(f"{table}: missing columns: {sorted(missing)}")
        item = {name: caster(row[name]) for name, caster in spec.items()}
        for key in METRIC_COLUMNS[table]:
            value = float(item[key])
            if not math.isfinite(value) or value <= 0:
                raise ValidationError(f"{table}.{key} must be finite positive")
        converted.append(item)
    return converted


def write_rows(path: Path, table: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SCHEMAS[table]))
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path, table: str) -> list[dict[str, object]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return coerce_rows(table, list(csv.DictReader(handle)))
```

- [ ] **Step 4: Implement per-table completeness, uniqueness, and source validation**

Use explicit expected sets:

```python
FIG6_COVERAGE = {0, 25, 50, 70, 80, 90, 100}
FIG7_RATIOS = set(range(0, 101, 10))
FIG7_PROTOCOLS = {"Tigon", "DS2PL+", "Sundial+"}
FIG8_BACKENDS = {"SHM", "TCP"}
FIG8_POLICIES = {"Baseline", "Interleave", "NUMA", "Frequency", "PageTableAware",
                 "FIFO", "HeatAware", "Hybrid", "Locality", "CacheFrequency",
                 "HugePage", "Lifetime", "LoadBalance"}


def validate_rows(table: str, rows: list[dict[str, object]], allow_mixed: bool = False) -> None:
    if not rows:
        raise ValidationError(f"{table}: no rows")
    sources = {row["source"] for row in rows}
    if not allow_mixed and len(sources) != 1:
        raise ValidationError(f"{table}: mixed source types: {sorted(sources)}")
    validator = VALIDATORS[table]
    validator(rows)
```

Each validator must reject duplicate primary keys and compare the observed sweep Cartesian product against the expected product for every repetition.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m pytest tests/figures_6_9/test_validation.py -q`

Expected: all tests pass.

```bash
git add script/figures_6_9/schemas.py script/figures_6_9/validation.py tests/figures_6_9/test_validation.py
git commit -m "repro: validate figures 6-9 result tables"
```

### Task 3: Shell-free execution and provenance manifests

**Files:**
- Create: `script/figures_6_9/execution.py`
- Create: `script/figures_6_9/provenance.py`
- Create: `tests/figures_6_9/test_execution.py`
- Create: `tests/figures_6_9/test_provenance.py`

- [ ] **Step 1: Write failing execution tests**

```python
from pathlib import Path
import sys

from figures_6_9.execution import run_command


def test_run_command_captures_combined_output(tmp_path: Path):
    result = run_command([sys.executable, "-c", "print('ok', end='')"],
                         tmp_path / "raw.log", {}, 5, False)
    assert result.returncode == 0
    assert result.output == "ok"
    assert (tmp_path / "raw.log").read_text() == "ok"


def test_dry_run_does_not_execute(tmp_path: Path):
    marker = tmp_path / "marker"
    result = run_command(["touch", str(marker)], tmp_path / "raw.log", {}, 5, True)
    assert result.dry_run is True
    assert not marker.exists()
```

- [ ] **Step 2: Implement immutable execution results**

```python
@dataclass(frozen=True)
class RunResult:
    argv: tuple[str, ...]
    returncode: int
    output: str
    started_utc: str
    ended_utc: str
    dry_run: bool


def run_command(argv: list[str], log_path: Path, env: dict[str, str], timeout_s: int,
                dry_run: bool) -> RunResult:
    started = datetime.now(timezone.utc).isoformat()
    if dry_run:
        return RunResult(tuple(argv), 0, "", started, started, True)
    process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=os.environ | env, timeout=timeout_s,
                             check=False, shell=False)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(process.stdout, encoding="utf-8")
    return RunResult(tuple(argv), process.returncode, process.stdout, started,
                     datetime.now(timezone.utc).isoformat(), False)
```

Catch `TimeoutExpired`, save partial output, and raise `ReproductionError` naming the command and timeout.

- [ ] **Step 3: Write and implement provenance tests**

The test asserts that `sha256_file()` matches `hashlib.sha256(data).hexdigest()` and that `build_manifest()` contains `git_revision`, `git_dirty`, UTC timestamps, configuration digest, platform, source, commands, and produced-file checksums.

Implement Git queries with `subprocess.run(["git", ...], cwd=repo_root, shell=False)` and never fail collection solely because the tree is dirty; record the dirty state.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m pytest tests/figures_6_9/test_execution.py tests/figures_6_9/test_provenance.py -q`

Expected: all tests pass.

```bash
git add script/figures_6_9/execution.py script/figures_6_9/provenance.py tests/figures_6_9/test_execution.py tests/figures_6_9/test_provenance.py
git commit -m "repro: capture commands and figure provenance"
```

### Task 4: Figure 6 TPC-C and Figure 7 YCSB collectors

**Files:**
- Create: `script/figures_6_9/collectors/__init__.py`
- Create: `script/figures_6_9/collectors/common.py`
- Create: `script/figures_6_9/collectors/fig6_tpcc.py`
- Create: `script/figures_6_9/collectors/fig7_ycsb.py`
- Create: `tests/figures_6_9/test_tigon_collectors.py`

- [ ] **Step 1: Write failing parser tests around configurable named-group regexes**

```python
from figures_6_9.collectors.fig6_tpcc import parse_tpcc
from figures_6_9.collectors.fig7_ycsb import parse_ycsb


def test_parse_tpcc_emits_node_rows():
    text = "node=0 NewOrder throughput=6123 txn/s\nnode=1 NewOrder throughput=6177 txn/s\n"
    rows = parse_tpcc(text, 0, 2, "measured")
    assert rows[0] == {"coverage_pct": 0, "node_id": 0,
                       "throughput_txn_s": 6123.0, "repetition": 2,
                       "source": "measured"}


def test_parse_ycsb_rejects_latency_only_output():
    with pytest.raises(ValidationError, match="throughput records"):
        parse_ycsb("AverageLatency(us)=12.0", "Tigon", 0, 0, "measured")
```

- [ ] **Step 2: Implement strict parsers and command planners**

Use compiled default patterns with named fields and permit an override from TOML:

```python
TPC_PATTERN = re.compile(
    r"node=(?P<node_id>\d+)\s+NewOrder\s+throughput=(?P<throughput>[0-9.]+)\s+txn/s"
)


def parse_tpcc(text: str, coverage_pct: int, repetition: int, source: str,
               pattern: re.Pattern[str] = TPC_PATTERN) -> list[dict[str, object]]:
    rows = [{"coverage_pct": coverage_pct,
             "node_id": int(match["node_id"]),
             "throughput_txn_s": float(match["throughput"]),
             "repetition": repetition, "source": source}
            for match in pattern.finditer(text)]
    if len(rows) != 2:
        raise ValidationError(f"fig6: expected two node throughput records, got {len(rows)}")
    return rows
```

`plan_tpcc_commands()` expands one argument vector per coverage and repetition. `plan_ycsb_commands()` expands the Cartesian product of protocol, ratio, and repetition. Neither function invokes a shell.

- [ ] **Step 3: Add collector execution tests with a temporary executable fixture**

Create a temporary Python executable that prints the expected result lines, run the collectors, and assert raw logs plus normalized rows. Also assert that a missing configured Tigon workdir raises `UnavailableError` with the `fig6.workdir` or `fig7.workdir` key.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m pytest tests/figures_6_9/test_tigon_collectors.py -q`

Expected: all tests pass.

```bash
git add script/figures_6_9/collectors tests/figures_6_9/test_tigon_collectors.py
git commit -m "repro: collect TPC-C and YCSB figure data"
```

### Task 5: Figure 8 GROMACS collector and invalid-artifact rejection

**Files:**
- Create: `script/figures_6_9/collectors/fig8_gromacs.py`
- Create: `tests/figures_6_9/test_gromacs_collector.py`

- [ ] **Step 1: Write failing success and repository-regression tests**

```python
from pathlib import Path
import pytest

from figures_6_9.collectors.fig8_gromacs import parse_gromacs
from figures_6_9.errors import ValidationError


def test_parse_completed_gromacs_run():
    text = "starting mdrun 'PEPSIN in water'\n10000 steps\nFinished mdrun\nWall time: 1.083 s\n"
    assert parse_gromacs(text, "SHM", "Baseline", 0, "measured")["elapsed_s"] == 1.083


@pytest.mark.parametrize("name", [
    "cxlmemsim.txt",
    "cxlmemsim_none_frequency_none_none.txt",
])
def test_rejects_committed_invalid_gromacs_logs(repo_root: Path, name: str):
    text = (repo_root / "artifact/gromacs/gmx" / name).read_text(errors="replace")
    with pytest.raises(ValidationError):
        parse_gromacs(text, "SHM", "Baseline", 0, "measured")
```

- [ ] **Step 2: Implement semantic completion checks before timing extraction**

```python
FATAL_MARKERS = ("Fatal error:", "terminate called", "TIMEOUT:")
WALL_TIME = re.compile(r"(?:Wall time:|Elapsed Time:)\s*(?P<seconds>[0-9.]+)\s*s")


def parse_gromacs(text: str, backend: str, policy: str, repetition: int,
                  source: str) -> dict[str, object]:
    if any(marker in text for marker in FATAL_MARKERS):
        raise ValidationError("fig8: fatal marker in GROMACS output")
    if "Number of Threads created: 0" in text:
        raise ValidationError("fig8: zero-work simulator run")
    if "Finished mdrun" not in text:
        raise ValidationError("fig8: missing GROMACS completion marker")
    match = WALL_TIME.search(text)
    if not match or float(match["seconds"]) <= 0:
        raise ValidationError("fig8: missing positive wall time")
    return {"backend": backend, "policy": policy,
            "elapsed_s": float(match["seconds"]), "repetition": repetition,
            "source": source}
```

The collector expands backend/policy/repetition commands from TOML, captures logs, parses them, and writes no normalized row until all categories complete.

- [ ] **Step 3: Run tests and commit**

Run: `python3 -m pytest tests/figures_6_9/test_gromacs_collector.py -q`

Expected: all tests pass, including rejection of both committed invalid log forms.

```bash
git add script/figures_6_9/collectors/fig8_gromacs.py tests/figures_6_9/test_gromacs_collector.py
git commit -m "repro: collect and validate GROMACS policy runs"
```

### Task 6: Figure 9 MPI+DAX benchmark and parser

**Files:**
- Create: `microbench/cxl_switch_lock_bench_mpi.c`
- Modify: `microbench/CMakeLists.txt`
- Create: `script/figures_6_9/collectors/fig9_logp.py`
- Create: `tests/figures_6_9/test_logp_collector.py`

- [ ] **Step 1: Write failing JSONL parser and LogP derivation tests**

```python
from figures_6_9.collectors.fig9_logp import derive_logp, parse_jsonl


def test_derive_logp_matches_paper_equation():
    params = derive_logp(os_ns=18.0, or_ns=438.0, rtt_ns=1200.0, g_ns=4.6)
    assert params["L_ns"] == 144.0
    assert params["bandwidth_gbps"] == pytest.approx(64.0 / 4.6)


def test_parse_jsonl_splits_samples_and_contention():
    text = '\n'.join([
        '{"type":"sample","operation":"os","sample_id":0,"latency_ns":18.0}',
        '{"type":"contention","lock_count":1,"effective_utilization":1.0,"added_latency_ns":1400.0}',
    ])
    parsed = parse_jsonl(text, source="measured", repetition=0)
    assert parsed.samples[0]["operation"] == "os"
    assert parsed.contention[0]["lock_count"] == 1
```

- [ ] **Step 2: Add optional MPI target to CMake**

Append:

```cmake
find_package(MPI COMPONENTS C QUIET)
if (MPI_C_FOUND)
    add_executable(cxl_switch_lock_bench_mpi cxl_switch_lock_bench_mpi.c)
    target_compile_options(cxl_switch_lock_bench_mpi PRIVATE -std=gnu11 -Wall -Wextra -mclflushopt)
    target_link_libraries(cxl_switch_lock_bench_mpi PRIVATE MPI::MPI_C)
else()
    message(STATUS "MPI C not found: cxl_switch_lock_bench_mpi will not be built")
endif()
```

- [ ] **Step 3: Implement a non-destructive benchmark CLI and local self-test**

The C program accepts `--dax PATH`, `--iterations N`, `--map-offset BYTES`, `--map-size BYTES`, `--acknowledge-dax-write`, and `--self-test`. It requires exactly two MPI ranks outside self-test and refuses DAX access unless the acknowledgement flag is present. It opens the device `O_RDWR|O_SYNC`, validates page alignment, maps only the configured range with `MAP_SHARED`, and never truncates or formats it. The Python collector independently requires `acknowledge_dax_writes = true` and prints the exact byte range before launch.

Use these timing and operation primitives:

```c
static inline uint64_t ns_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static inline void flush_sender(void *p) {
    _mm_clflushopt(p);
    _mm_sfence();
}

static inline uint64_t invalidate_load(volatile uint64_t *p) {
    _mm_clflush((const void *)p);
    _mm_mfence();
    return *p;
}

static void emit_sample(const char *operation, uint64_t sample_id, double latency_ns) {
    printf("{\"type\":\"sample\",\"operation\":\"%s\","
           "\"sample_id\":%" PRIu64 ",\"latency_ns\":%.3f}\n",
           operation, sample_id, latency_ns);
}
```

Implement phases for `os`, `cas_raw`, `cas_flush`, `or`, `full_rt`, ping-pong RTT, 512-line streaming flush, and CAS contention with lock counts `1,2,4,8`. Surround phases with `MPI_Barrier`, emit JSONL only from rank 0, and print errors to stderr. `--self-test` uses one anonymous page and verifies every operation produces a finite positive duration without accessing DAX.

- [ ] **Step 4: Implement the Python parser, parameter derivation, and collector gate**

```python
def derive_logp(os_ns: float, or_ns: float, rtt_ns: float, g_ns: float) -> dict[str, float]:
    latency = (rtt_ns - 2.0 * os_ns - 2.0 * or_ns) / 2.0
    if latency <= 0 or g_ns <= 0:
        raise ValidationError("fig9: invalid measured LogP components")
    return {"o_s_ns": os_ns, "L_ns": latency, "o_r_ns": or_ns,
            "g_ns": g_ns, "bandwidth_gbps": 64.0 / g_ns}
```

Require all five operation names, at least the configured sample count per operation, one RTT and gap summary, both MPI ranks in metadata, and all four contention lock counts.

Build the three Figure 9b scenarios from configuration and measurement: `Default` uses the configured pre-calibration values, `Real HW` uses derived measured values, and `OCEAN calibrated` uses the values written back by calibration. Build Figure 9c's default curve with the current `FabricLink` piecewise rule and the calibrated curve by piecewise-linear interpolation through the measured points plus the origin:

```python
def default_contention_ns(utilization: float) -> float:
    if utilization < 0.5:
        return 0.0
    if utilization < 0.8:
        return (utilization - 0.5) / 0.3 * 20.0
    return 20.0 + (utilization - 0.8) / 0.2 * 80.0


def calibrated_curve(measured: list[tuple[float, float]]) -> list[tuple[float, float]]:
    points = sorted([(0.0, 0.0), *measured])
    x = np.linspace(0.0, 1.0, 101)
    y = np.interp(x, [point[0] for point in points], [point[1] for point in points])
    return list(zip(x.tolist(), y.tolist(), strict=True))
```

- [ ] **Step 5: Build, self-test, run parser tests, and commit**

Run:

```bash
cmake -S . -B build -DLEGOMEM_BUILD_QEMU_INTEGRATION=OFF
cmake --build build --target cxl_switch_lock_bench_mpi -j2
./build/microbench/cxl_switch_lock_bench_mpi --self-test --iterations 32
python3 -m pytest tests/figures_6_9/test_logp_collector.py -q
```

Expected: target builds, self-test exits zero with finite samples, and Python tests pass. This is not cross-host proof.

```bash
git add microbench/CMakeLists.txt microbench/cxl_switch_lock_bench_mpi.c script/figures_6_9/collectors/fig9_logp.py tests/figures_6_9/test_logp_collector.py
git commit -m "repro: add two-host LogP calibration benchmark"
```

### Task 7: Paper-matched plotting for all six panels

**Files:**
- Create: `script/figures_6_9/plotting.py`
- Create: `tests/figures_6_9/test_plotting.py`

- [ ] **Step 1: Generate complete synthetic tables in a pytest fixture**

Add a `normalized_fixture` fixture that writes all six CSV tables using `write_rows()`. Use deterministic positive formulas, for example Figure 6 node throughput `6000 + coverage_pct * 600`, Figure 7 throughput `6000 + protocol_offset - write_ratio_pct * slope`, Figure 8 elapsed `1.0 + backend_offset + policy_index * 0.005`, and 100 monotonic Figure 9 samples per operation.

- [ ] **Step 2: Write failing plot smoke tests**

```python
from figures_6_9.plotting import plot_all


def test_plot_all_writes_six_pdf_and_png_pairs(normalized_fixture, tmp_path):
    outputs = plot_all(normalized_fixture, tmp_path, formats=("pdf", "png"),
                       show_error_bars=False)
    assert {path.stem for path in outputs} == {
        "fig6", "fig7", "fig8", "fig9a", "fig9b", "fig9c"
    }
    assert len(outputs) == 12
    assert all(path.stat().st_size > 1000 for path in outputs)
```

- [ ] **Step 3: Implement reusable style, aggregation, and plot functions**

Use `matplotlib.use("Agg")` before importing pyplot. Implement `mean_by(rows, keys, metric)` with `statistics.fmean` and `statistics.stdev`. Add `plot_fig6`, `plot_fig7`, `plot_fig8`, `plot_fig9a`, `plot_fig9b`, and `plot_fig9c`; each returns its `Figure` for label assertions and closes it after saving.

The Figure 9 CDF helper is:

```python
def empirical_cdf(values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(values, dtype=float))
    y = np.arange(1, len(x) + 1, dtype=float) / len(x)
    return x, y
```

Figure 9b computes stack totals from `o_s_ns`, `L_ns`, and `o_r_ns`; it must not embed 190 ns or 598 ns as plot constants. Figure 9c shades only the overlap between measured and default x coordinates.

- [ ] **Step 4: Assert labels and order, then run tests**

Add assertions for exact x labels, axis units, legend series, Figure 8 policy order, and Figure 9 operation names.

Run: `MPLCONFIGDIR=/tmp/ocean-mpl python3 -m pytest tests/figures_6_9/test_plotting.py -q`

Expected: all tests pass and twelve nonempty files are produced.

- [ ] **Step 5: Commit**

```bash
git add script/figures_6_9/plotting.py tests/figures_6_9/test_plotting.py
git commit -m "repro: plot OCEAN paper figures 6-9"
```

### Task 8: CLI orchestration, doctor, manifests, and documentation

**Files:**
- Create: `script/figures_6_9/cli.py`
- Create: `script/reproduce_figures_6_9.py`
- Create: `tests/figures_6_9/test_cli.py`
- Create: `docs/source/reproducing-figures-6-9.rst`
- Modify: `docs/source/index.rst`

- [ ] **Step 1: Write failing CLI tests**

```python
from figures_6_9.cli import main


def test_doctor_reports_missing_tigon_without_mutation(repo_root, capsys):
    rc = main(["doctor", "--fig", "6",
               "--config", str(repo_root / "script/figures_6_9/config.example.toml")])
    assert rc == 2
    assert "fig6.workdir" in capsys.readouterr().err


def test_plot_only_succeeds_from_validated_fixture(normalized_fixture, tmp_path):
    rc = main(["plot", "--fig", "all", "--input", str(normalized_fixture),
               "--output", str(tmp_path), "--format", "png"])
    assert rc == 0
    assert (tmp_path / "fig9c.png").is_file()


def test_plot_accepts_paper_style_error_bar_toggle(normalized_fixture, tmp_path):
    rc = main(["plot", "--fig", "6", "--input", str(normalized_fixture),
               "--output", str(tmp_path), "--format", "png", "--show-error-bars"])
    assert rc == 0
```

- [ ] **Step 2: Implement `doctor` with read-only checks**

Check Python imports, configured workdirs, first command executables, hostfiles, `mpirun`, MPI benchmark path, and DAX character-device type. Return structured findings with `ok`, `unavailable`, or `invalid`. Do not invoke setup scripts, SSH, `sudo`, package managers, network configuration, or DAX configuration.

- [ ] **Step 3: Implement CLI commands and stable exit codes**

```python
EXIT_OK = 0
EXIT_UNAVAILABLE = 2
EXIT_INVALID = 3
EXIT_EXECUTION = 4


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
```

`build_parser()` must expose `doctor`, `collect`, `validate`, `plot`, and `all`; shared `--fig` choices are `6,7,8,9,all`; `plot` accepts `--input`, `--output`, repeated `--format {pdf,png}`, `--allow-mixed-sources`, and `--show-error-bars`. `all` must call the same Python functions as individual commands, stop on the first failed gate, and create `manifest.json` only after normalized tables and plots are checksummed. `plot` accepts an existing normalized directory and never triggers collection.

- [ ] **Step 4: Add executable repository wrapper**

```python
#!/usr/bin/env python3
from figures_6_9.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Document setup, examples, and proof boundaries**

The RST page must include:

```rst
Plot validated results only
---------------------------

.. code-block:: bash

   python3 script/reproduce_figures_6_9.py validate --fig all --input artifact/figures_6_9/20260804T120000Z/normalized
   python3 script/reproduce_figures_6_9.py plot --fig all --input artifact/figures_6_9/20260804T120000Z/normalized

``doctor`` and synthetic plot tests do not prove Tigon, GROMACS, or real CXL measurements. Figure 9 requires two hosts mapping the same CXL Type-3 DAX range.
```

Add `reproducing-figures-6-9` to the `index.rst` documentation list and hidden toctree.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
python3 -m pytest tests/figures_6_9/test_cli.py -q
python3 script/reproduce_figures_6_9.py --help
python3 script/reproduce_figures_6_9.py doctor --fig all --config script/figures_6_9/config.example.toml
```

Expected: CLI tests pass, help lists five commands, and `doctor` exits 2 with actionable missing-external-prerequisite messages on this checkout.

```bash
git add script/reproduce_figures_6_9.py script/figures_6_9/cli.py tests/figures_6_9/test_cli.py docs/source/reproducing-figures-6-9.rst docs/source/index.rst
git commit -m "repro: expose figures 6-9 workflow CLI"
```

### Task 9: Full regression and artifact inspection

**Files:**
- Modify only files required by failures found in this task.

- [ ] **Step 1: Run the focused Python suite**

Run: `MPLCONFIGDIR=/tmp/ocean-mpl python3 -m pytest tests/figures_6_9 -q`

Expected: all tests pass.

- [ ] **Step 2: Run repository tests without QEMU integration**

Run:

```bash
cmake -S . -B build -DLEGOMEM_BUILD_QEMU_INTEGRATION=OFF
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

Expected: build succeeds; existing CTest tests pass; MPI benchmark is built when MPI C is found or CMake prints the explicit optional-target message.

- [ ] **Step 3: Exercise the local proof boundary**

Run:

```bash
./build/microbench/cxl_switch_lock_bench_mpi --self-test --iterations 32
python3 script/reproduce_figures_6_9.py doctor --fig all --config script/figures_6_9/config.example.toml
```

Expected: self-test passes. `doctor` reports unavailable Tigon and/or DAX dependencies and exits 2; this expected unavailability is recorded, not treated as measured reproduction.

- [ ] **Step 4: Inspect generated synthetic plots**

Run the plotting test with `--basetemp=/tmp/ocean-figures-6-9`, locate its output directory, and inspect all six PNGs for clipped labels, incorrect category order, or unreadable legends. Fix plot layout only if inspection reveals a concrete defect, then rerun the plotting suite.

- [ ] **Step 5: Check repository cleanliness and commit fixes**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional source changes are present; `artifact/figures_6_9/`, caches, and build output are untracked only if already permitted by ignore rules.

If Task 9 required source fixes, stage only the known workflow paths:

```bash
git add script/reproduce_figures_6_9.py script/figures_6_9 microbench/CMakeLists.txt microbench/cxl_switch_lock_bench_mpi.c tests/figures_6_9 docs/source/reproducing-figures-6-9.rst docs/source/index.rst
git commit -m "repro: fix figures 6-9 verification issues"
```

Do not create an empty commit when no fixes were needed.
