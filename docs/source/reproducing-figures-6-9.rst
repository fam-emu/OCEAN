Reproducing Figures 6--9
========================

The repository provides one fail-closed workflow for collecting, validating, and
plotting Figures 6--9 from ``OCEAN.pdf``. It preserves raw command output,
normalized CSV tables, plot files, and a checksum manifest under
``artifact/figures_6_9/<run-id>/``.

Prerequisites and configuration
-------------------------------

Copy ``script/figures_6_9/config.example.toml`` and edit the workload paths and
commands for the installed Tigon and GROMACS revisions. Commands are TOML arrays;
they are executed directly without a shell. Check the resulting configuration
without changing the host:

.. code-block:: bash

   python3 script/reproduce_figures_6_9.py doctor --fig all --config figures.toml

Figures 6 and 7 require the external Tigon tree. Figure 8 requires a working
PEPSIN/GROMACS runner for every backend and policy. The committed GROMACS logs
are not accepted as measurements because they contain fatal or zero-thread runs.

The example configuration uses the installed sibling
``../../CXLMemSim/workloads/tigon`` tree through the Python adapters in
``script/figures_6_9/runners``. The adapters synchronize the exact local Tigon
binary into two VMs and retain both node summaries. The metadata-owning node is
launched first and must publish fresh CXL transport metadata before the peer
starts, matching Tigon's raw-DAX
runner and preventing stale offsets between sweep points. Figure 7 uses Tigon's
2,048-byte transport entries for ``TwoPLPasha`` and its 65,536-byte baseline
entries for ``TwoPL`` and ``Sundial``. Tigon's HCC-budget script uses the
``mixed`` TPC-C query;
therefore the collected ``average commit`` metric is the aggregate TPC-C commit
rate used by that script even though Figure 6 labels the axis NewOrder.

Figure 9 requires MPI on two hosts that map the same real CXL Type-3 DAX range.
Build its optional target with:

.. code-block:: bash

   cmake -S . -B build -DLEGOMEM_BUILD_QEMU_INTEGRATION=OFF
   cmake --build build --target cxl_switch_lock_bench_mpi -j2
   ./build/microbench/cxl_switch_lock_bench_mpi --self-test --iterations 32

The self-test uses anonymous memory and proves only that the local benchmark
binary executes. Hardware collection refuses to open DAX until
``fig9.acknowledge_dax_writes = true`` and the command contains
``--acknowledge-dax-write``. It maps only the configured, page-aligned
``[map_offset, map_offset + map_size)`` byte range. It never creates, formats,
resizes, or mounts a DAX device, but it *does write* inside that mapped range.
For DAX devices owned by a VM runner, set ``fig9.dax_scope = "runner"``. In
that mode ``doctor`` validates the local benchmark and delegates the character
device check to the runner immediately before execution.

Collect and reproduce
---------------------

Preview the resolved commands, then run one figure or the complete workflow:

.. code-block:: bash

   python3 script/reproduce_figures_6_9.py collect --fig 6 --config figures.toml --dry-run
   python3 script/reproduce_figures_6_9.py all --fig all --config figures.toml

Collection stops on nonzero process exit, malformed output, incomplete sweeps,
invalid GROMACS completion evidence, incomplete MPI rank metadata, or unsafe DAX
configuration. Normalized tables are written only after the selected sweep
passes validation. The manifest records the Git revision, configuration digest,
host, commands, timestamps, source type, and output checksums.

Plot validated results only
---------------------------

Validation and plotting never launch workloads:

.. code-block:: bash

   python3 script/reproduce_figures_6_9.py validate --fig all \
      --input artifact/figures_6_9/20260804T120000Z/normalized
   python3 script/reproduce_figures_6_9.py plot --fig all \
      --input artifact/figures_6_9/20260804T120000Z/normalized \
      --output artifact/figures_6_9/20260804T120000Z/plots \
      --format pdf --format png

The default source-integrity gate rejects tables that mix ``measured``,
``paper_reference``, and ``synthetic`` rows. ``--allow-mixed-sources`` is an
explicit override for comparison plots; it does not relabel any row.

Proof boundary
--------------

Passing Python tests, ``doctor``, plotting synthetic fixtures, or running the
anonymous-memory Figure 9 self-test does not prove Tigon, GROMACS, or cross-host
CXL measurements. A measured reproduction requires successful workload logs and,
for Figure 9, two-host execution against the shared real CXL Type-3 DAX range.
