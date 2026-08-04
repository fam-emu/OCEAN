# OCEAN Figures 6–9 Reproduction Design

## Goal

Add a reproducible, provenance-preserving workflow for generating Figures 6–9 from `OCEAN.pdf`. The workflow must support both end-to-end experiment collection and plot-only use with previously collected results. It must never present failed, incomplete, synthetic, or paper-derived values as measured results.

The figures in scope are:

- Figure 6: per-node TPC-C NewOrder throughput versus hardware cache-coherence coverage.
- Figure 7: YCSB throughput versus write ratio for Tigon, DS2PL+, and Sundial+.
- Figure 8: GROMACS PEPSIN execution time for SHM and TCP across the plotted baseline and twelve alternative memory-placement policies.
- Figure 9a: CDFs of cross-host CXL operation latency.
- Figure 9b: default, measured, and calibrated LogP latency decomposition.
- Figure 9c: contention latency versus effective utilization.

## Current Repository Boundary

The current checkout does not contain enough valid data to claim measured reproduction:

- The Tigon workload used by Figures 6 and 7 is fetched by `script/setup_host.sh` and is not present in this checkout.
- The committed GROMACS policy logs are not valid Figure 8 measurements. The baseline contains a fatal GROMACS thread-count mismatch, and the policy logs report zero created threads.
- The paper names `cxl_switch_lock_bench_mpi` for Figure 9, but that benchmark is absent. The experiment also requires two hosts sharing a real CXL Type-3 DAX device.

The implementation will therefore provide a fully testable collection, validation, and plotting workflow while reporting unavailable external prerequisites explicitly. Local CLI and plotting success is not evidence that the hardware-dependent figures were measured.

## User Interface

The entry point will be:

```text
python3 script/reproduce_figures_6_9.py <command> [options]
```

It will expose five commands:

- `doctor`: perform read-only checks for Python packages, binaries, workload trees, host configuration, MPI, and DAX devices.
- `collect`: execute one figure's experiment adapters or all available adapters and preserve raw output.
- `validate`: validate raw and normalized results without plotting them.
- `plot`: generate figures from already normalized input.
- `all`: run `doctor`, `collect`, `validate`, and `plot` in sequence, stopping at the first failed gate.

All commands accept `--fig 6`, `--fig 7`, `--fig 8`, `--fig 9`, or `--fig all` where applicable. `collect` and `all` support `--dry-run`. Plotting accepts PDF and PNG output and defaults to both.

External paths, hosts, hostfiles, repetitions, timeouts, and workload arguments will live in a TOML configuration file. Commands will be represented as argument arrays with documented placeholders and executed without a shell. The default configuration will contain repository-relative paths but no machine-specific credentials or device setup commands.

## Repository Layout

```text
script/
  reproduce_figures_6_9.py
  figures_6_9/
    cli.py
    config.py
    execution.py
    provenance.py
    schemas.py
    validation.py
    plotting.py
    collectors/
      fig6_tpcc.py
      fig7_ycsb.py
      fig8_gromacs.py
      fig9_logp.py
    config.example.toml
microbench/
  cxl_switch_lock_bench_mpi.c
tests/
  figures_6_9/
    fixtures/
    test_cli.py
    test_parsers.py
    test_validation.py
    test_plotting.py
artifact/
  figures_6_9/<run-id>/
    raw/
    normalized/
    plots/
    manifest.json
```

Modules have narrow responsibilities: collectors run and parse workload-specific experiments; schemas define normalized records; validation enforces completeness and integrity; plotting only consumes validated records; provenance writes immutable run metadata.

## Data Flow and Schemas

Each experiment follows the same sequence:

1. `doctor` checks prerequisites without changing the machine.
2. A collector records its resolved argument vector and selected environment variables.
3. The process output, exit status, start/end times, and host identity are saved under `raw/`.
4. A workload-specific parser emits normalized CSV.
5. The validator checks process success, semantic completion markers, sweep completeness, and metric ranges.
6. Only validated CSV reaches the plotter.
7. The manifest records the source type, Git revision, configuration digest, and produced file checksums.

Normalized tables are:

- `fig6.csv`: `coverage_pct,node_id,throughput_txn_s,repetition,source`
- `fig7.csv`: `protocol,write_ratio_pct,throughput_txn_s,repetition,source`
- `fig8.csv`: `backend,policy,elapsed_s,repetition,source`
- `fig9_samples.csv`: `operation,sample_id,latency_ns,source`
- `fig9_params.csv`: `scenario,o_s_ns,L_ns,o_r_ns,g_ns,bandwidth_gbps,source`
- `fig9_contention.csv`: `series,lock_count,effective_utilization,added_latency_ns,repetition,source`

`source` is one of `measured`, `paper_reference`, or `synthetic`. A plot may use only one source type unless the user explicitly requests a comparison and the legend identifies every source. Test fixtures use `synthetic`. Paper-digitized sample data will not be labeled measured.

## Figure Collectors

### Figure 6

The TPC-C collector runs the configured Two-Phase Locking NewOrder workload for every configured HCC coverage point and repetition. It records node-level throughput and derives total throughput from node records instead of trusting a separately printed total. The default sweep is `0,25,50,70,80,90,100`, matching the points plotted in the paper; the installed Tigon command mapping remains configuration-driven.

Because Tigon is external, the collector will fail `doctor` with an actionable message when the configured tree or runner is missing. It will not guess positional Tigon arguments from the paper.

### Figure 7

The YCSB collector runs the three configured protocols at write ratios from 0% through 100% in 10% increments. It validates that each protocol has every ratio and that the reported value is transaction throughput rather than an operation latency or aggregate from a different YCSB phase.

The protocol-to-command mapping resides in TOML so an installed Tigon revision can be described without hard-coding machine-specific commands in Python.

### Figure 8

The GROMACS collector runs PEPSIN for SHM and TCP using the thirteen categories shown on the paper's x-axis: Baseline, Interleave, NUMA, Frequency, PageTableAware, FIFO, HeatAware, Hybrid, Locality, CacheFrequency, HugePage, Lifetime, and LoadBalance. Baseline plus the twelve alternative policies are represented explicitly in configuration; the plot labels match the plotted data even though the caption summarizes them as twelve policies.

Successful collection requires a zero process exit status, a GROMACS completion marker, a positive elapsed time, and evidence that useful work ran. Fatal errors, zero-step/zero-thread runs, and simulator-only shutdown statistics are rejected. The existing invalid artifact logs remain untouched and are not default plot inputs.

### Figure 9

The missing `cxl_switch_lock_bench_mpi` benchmark will be added as an optional MPI C target. It maps a configured DAX device on two hosts and uses MPI only for phase coordination; timed operations use direct CXL memory access.

The benchmark records samples for `clflushopt+sfence`, raw CAS, flush+CAS, `clflush+mfence+load`, and the full store-flush-invalidate-load round trip. It also measures ping-pong RTT, streaming-flush gap, and contention for lock counts 1, 2, 4, and 8. Machine-readable output includes benchmark version, rank, CPU, DAX path, iteration count, and phase.

The collector derives LogP values using the equations stated in the paper and cross-checks them against benchmark summaries. It never creates or configures a DAX region, mounts a device, installs MPI, or performs privileged remote setup.

## Plotting

Matplotlib will generate vector PDF and high-resolution PNG files. Figure dimensions, labels, legends, colors, markers, ordering, and panel layout will follow the paper while remaining readable outside the two-column manuscript.

- Figure 6 plots both nodes and their derived total against HCC coverage.
- Figure 7 plots the three protocols against write ratio.
- Figure 8 plots grouped SHM/TCP bars in the paper's policy order.
- Figure 9a uses empirical CDFs from raw samples.
- Figure 9b uses stacked bars computed from the parameter table.
- Figure 9c shows default, calibrated, and measured contention series, with the default-versus-measured gap shaded only where both are defined.

The normalized CSV retains repetitions and dispersion. Paper-matched plots default to means without error bars because the published figures omit them; `--show-error-bars` exposes standard deviation when multiple repetitions are available.

## Failure Handling and Provenance

The workflow fails closed. It rejects:

- nonzero process exit status or timeout;
- fatal-error, abort, or incomplete-run markers;
- missing sweep points or duplicate primary keys;
- non-finite or nonpositive latency, throughput, and elapsed-time values;
- Figure 8 logs without completed GROMACS work;
- Figure 9 runs with missing ranks, phases, or insufficient samples;
- mixed source types unless explicitly requested.

`doctor` is read-only. No command installs packages, runs host setup, changes networking, creates DAX regions, mounts filesystems, or invokes `sudo`. Error messages identify the missing prerequisite and the exact configuration key involved.

The manifest contains the OCEAN Git revision, dirty-state flag, UTC timestamps, hostname, platform, Python version, configuration digest, resolved commands, allowlisted environment settings, exit statuses, source type, and SHA-256 checksum of every normalized table and plot.

## Testing and Verification

Tests use synthetic fixtures that cover success and expected failures for every parser and validator. They verify:

- CLI dispatch, `--dry-run`, and per-figure selection;
- parsing of representative Tigon, YCSB, GROMACS, and MPI outputs;
- rejection of the failure patterns already present in committed GROMACS logs;
- schema types, uniqueness, completeness, allowed categories, and ranges;
- LogP derivation and Figure 9 stacked-bar totals;
- creation of all six panels with expected axes, labels, series, and nonempty PDF/PNG files;
- deterministic ordering and manifest checksums.

A local integration test runs `doctor`, validates fixtures, and generates all plots without hardware. Hardware-dependent collection is a separate proof gate and is reported as skipped or unavailable, never passed, when its prerequisites are absent.

## Acceptance Criteria

The implementation is complete when:

1. The CLI and all five commands are documented and tested.
2. Every figure has an isolated collector, normalized schema, validator, and plotter.
3. The Figure 9 benchmark builds when MPI C development support is available and is omitted with a clear CMake status when it is not.
4. Invalid committed GROMACS logs are rejected by tests.
5. Synthetic fixtures generate Figure 6, Figure 7, Figure 8, and Figure 9a–c in PDF and PNG.
6. `doctor` reports the current machine's missing Tigon or CXL prerequisites without mutation.
7. No output can be mistaken for measured data without a measured manifest and successful validation.

Actual paper-equivalent measured values are not an acceptance criterion on a machine without the required Tigon deployment and two-host CXL hardware. They become a separate run-time validation once those resources are supplied.
