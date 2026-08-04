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
