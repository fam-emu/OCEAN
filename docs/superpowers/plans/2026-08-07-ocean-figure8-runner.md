# OCEAN Figure 8 Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing fail-closed Figure 8 cell runner used by the existing SHM/TCP x thirteen-policy collector.

**Architecture:** Keep sweep orchestration, logging, normalization, and plotting in `script/figures_6_9`. Add one shell runner in the configured GROMACS work directory that validates inputs, maps the paper label to the legacy four-slot policy tuple, selects an explicit SHM or TCP adapter, and passes a policy-wrapped GROMACS command to that adapter. Tests use fake executables and never launch a workload.

**Tech Stack:** Bash 4+, Python 3, pytest, existing CXLMemSim legacy CLI and Figures 6--9 Python collector.

---

### Task 1: Lock the cell-runner contract with tests

**Files:**
- Create: `tests/figures_6_9/test_fig8_runner.py`

- [ ] **Step 1: Write a fake-launcher test helper**

Create executable temporary files for the SHM launcher, TCP launcher,
`cxlmemsim_legacy`, and `gmx_mpi`, plus a readable `benchMEM.tpr`. The fake
launcher prints each received argument on its own `ARG=<value>` line and exits
with `FAKE_LAUNCHER_RC`.

- [ ] **Step 2: Write the failing policy-mapping test**

Parametrize the exact label-to-tuple table from the design. Invoke
`workloads/gromacs/run_figure8.sh --backend SHM --policy <label>` with the fake
paths and assert that output contains the selected backend, label, tuple, and
the `-k` argument followed by that tuple.

- [ ] **Step 3: Write the failing backend and validation tests**

Assert SHM selects only the SHM launcher, TCP selects only the TCP launcher,
unsupported labels/backends fail, missing launchers fail, non-positive numeric
settings fail, and the fake launcher's nonzero status is propagated.

- [ ] **Step 4: Verify RED**

Run:

```bash
python3 -m pytest tests/figures_6_9/test_fig8_runner.py -q
```

Expected: failures because `workloads/gromacs/run_figure8.sh` does not exist.

### Task 2: Implement the Figure 8 cell runner

**Files:**
- Create: `workloads/gromacs/run_figure8.sh`

- [ ] **Step 1: Add strict parsing and validation**

Use `set -euo pipefail`; accept only `--backend`, `--policy`, and `--help`;
require backend and policy exactly once; validate executable/readable paths and
positive integer step/thread/sample settings; reject whitespace in paths
because the legacy `-t` parser tokenizes on spaces.

- [ ] **Step 2: Add the exact policy map**

Implement a `case` statement with all thirteen tuples from the design and fail
for any other label.

- [ ] **Step 3: Build the target and wrapper argument arrays**

Serialize this target as the single argument required by legacy `-t`:

```text
/usr/bin/env OMP_NUM_THREADS=<n> HOME=<home> PATH=<path> <gmx> mdrun -s <tpr> -nsteps <steps> -resethway -ntomp <n> -noconfout -noappend
```

Build the outer array as:

```text
<cxlmemsim> -c <cpuset> -p <period> -k <tuple> -t <target-string>
```

Invoke only the selected backend adapter as `<launcher> -- <outer-array>`.
Print backend/policy/tuple provenance before execution and print
`Finished mdrun` only after a zero adapter exit.

- [ ] **Step 4: Verify GREEN without running a workload**

Run:

```bash
bash -n workloads/gromacs/run_figure8.sh
python3 -m pytest tests/figures_6_9/test_fig8_runner.py -q
```

Expected: shell syntax succeeds and all runner tests pass using fake tools.

### Task 3: Document submission usage and verify integration

**Files:**
- Modify: `workloads/gromacs/README.md`
- Test: `tests/figures_6_9/test_gromacs_collector.py`

- [ ] **Step 1: Document backend-adapter and environment contracts**

Add a Figure 8 section showing how to export `FIG8_CXLMEMSIM`, `FIG8_GMX_MPI`,
`FIG8_TPR`, `FIG8_SHM_LAUNCHER`, and `FIG8_TCP_LAUNCHER`, followed by the
existing `collect --fig 8` and `plot --fig 8` commands. State that adapters must
provide real backend selection and that the runner refuses to invent a TCP
series.

- [ ] **Step 2: Verify the existing collector plan**

Run only non-workload tests:

```bash
python3 -m pytest tests/figures_6_9/test_fig8_runner.py tests/figures_6_9/test_gromacs_collector.py tests/figures_6_9/test_config.py -q
```

Expected: all tests pass; the existing config retains SHM/TCP and thirteen
policies.

- [ ] **Step 3: Inspect the final diff and commit**

Run:

```bash
git diff --check
git status --short
```

Commit only the runner, its tests, its README update, and this implementation
plan. Do not stage outer-repository artifacts or unrelated changes.

