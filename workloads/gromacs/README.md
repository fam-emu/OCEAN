# MPI CXL Memory Shim Layer

This shim layer intercepts OpenMPI calls and redirects memory operations to CXL memory using DAX devices, similar to the atomic test implementation in `/microbench/test_dax_litmus_atomic.c`.

## Features

- **Transparent interception** of MPI memory allocation and communication functions
- **DAX device support** for real CXL memory hardware
- **Shared memory fallback** when DAX devices are not available
- **Configurable behavior** through environment variables
- **Support for key MPI operations**:
  - Memory allocation (`MPI_Alloc_mem`, `MPI_Free_mem`)
  - Point-to-point communication (`MPI_Send`, `MPI_Recv`, `MPI_Isend`, `MPI_Irecv`)
  - RMA windows (`MPI_Win_allocate`, `MPI_Win_allocate_shared`)

## Building

```bash
cd <path/to/OCEAN>/workloads/gromacs
make
```

This will build:
- `libmpi_cxl_shim.so` - The shim library
- `test_mpi_cxl` - Test program

## Usage

### Environment Variables

Configure the shim layer behavior using these environment variables:

- `CXL_DAX_PATH`: Path to DAX device (e.g., `/dev/dax0.0`). If not set, uses shared memory fallback
- `CXL_MEM_SIZE`: Size of CXL memory pool (default: 4GB)
- `CXL_SHIM_VERBOSE`: Enable verbose logging (set to 1)
- `CXL_SHIM_ALLOC`: Redirect MPI_Alloc_mem to CXL memory (set to 1)
- `CXL_SHIM_WIN`: Redirect MPI window allocations to CXL memory (set to 1)
- `CXL_SHIM_COPY_SEND`: Copy send buffers to CXL memory (set to 1)
- `CXL_SHIM_COPY_RECV`: Use CXL memory for receive buffers (set to 1)

### Running with GROMACS

1. **With DAX device (real CXL hardware):**
```bash
export LD_PRELOAD=/path/to/libmpi_cxl_shim.so
export CXL_DAX_PATH=/dev/dax0.0
export CXL_SHIM_ALLOC=1
export CXL_SHIM_WIN=1
mpirun -np 4 gmx_mpi mdrun -s input.tpr
```

2. **With shared memory fallback:**
```bash
export LD_PRELOAD=/path/to/libmpi_cxl_shim.so
export CXL_MEM_SIZE=$((8*1024*1024*1024))  # 8GB
export CXL_SHIM_ALLOC=1
export CXL_SHIM_WIN=1
mpirun -np 4 gmx_mpi mdrun -s input.tpr
```

### Testing

Run the test program to verify the shim layer:

```bash
# Set up environment
export LD_PRELOAD=./libmpi_cxl_shim.so
export CXL_SHIM_VERBOSE=1
export CXL_SHIM_ALLOC=1
export CXL_SHIM_WIN=1

# Run test
mpirun -np 2 ./test_mpi_cxl
```

## Implementation Details

The shim layer works by:

1. **Dynamic linking interception**: Uses `LD_PRELOAD` to override MPI functions
2. **Memory mapping**: Maps either DAX devices or shared memory into the process space
3. **Address translation**: Maintains mappings between original buffers and CXL memory
4. **Atomic operations**: Ensures thread-safe memory allocation and mapping management

### Memory Layout

```
CXL Memory Region (DAX or SHM)
┌─────────────────────────────────┐
│  Allocation Area (sequential)    │
│  ┌──────────┐ ┌──────────┐      │
│  │ Alloc 1  │ │ Alloc 2  │ ...  │
│  └──────────┘ └──────────┘      │
└─────────────────────────────────┘
```

### Function Interception Flow

```
Application → MPI_Alloc_mem() → Shim Layer → CXL Memory
                                     ↓
                              Original MPI (fallback)
```

## Performance Considerations

- **Latency**: CXL memory access has higher latency than local DRAM
- **Bandwidth**: Limited by CXL link speed (typically 32-64 GB/s)
- **NUMA effects**: CXL memory appears as a separate NUMA node
- **Memory coherence**: Maintained by hardware, but with performance implications

## Debugging

Enable verbose output to trace shim operations:

```bash
export CXL_SHIM_VERBOSE=1
```

Check if the shim is loaded:

```bash
ldd your_mpi_program | grep libmpi_cxl_shim
```

## Integration with CXLMemSim

This shim layer integrates with the CXLMemSim framework:

- Uses the same DAX device interface as the litmus tests
- Compatible with CXLMemSim's shared memory mode
- Can be monitored using CXLMemSim's performance tracking tools

## Reproducing OCEAN Figure 8

`run_figure8.sh` is the per-cell runner used by the repository's Figures 6--9
workflow. The collector calls it for SHM and TCP across the thirteen policy
labels shown in the paper. The runner maps each label to the corresponding
allocation, migration, paging, and caching policy tuple for
`cxlmemsim_legacy`.

The current GROMACS shim has a shared-memory fallback but no TCP client, while
the current server does not expose the legacy four-slot policy interface.
Therefore, the runner deliberately requires separate executable backend
adapters instead of silently generating two identically configured series.
Each adapter must select or connect to the real backend and execute everything
after `--` without changing its argument boundaries:

```bash
export FIG8_CXLMEMSIM=/path/to/cxlmemsim_legacy
export FIG8_GMX_MPI=/path/to/gmx_mpi
export FIG8_TPR=/path/to/benchMEM.tpr
export FIG8_SHM_LAUNCHER=/path/to/launch-figure8-shm
export FIG8_TCP_LAUNCHER=/path/to/launch-figure8-tcp
```

Optional controls are `FIG8_STEPS` (default `10000`), `FIG8_NTOMP` (default
`1`), `FIG8_CPUSET` (default `0`), and `FIG8_PEBS_PERIOD` (default `1000`). A
backend adapter receives this interface:

```text
launch-figure8-shm -- cxlmemsim_legacy -c ... -p ... -k ... -t ...
```

From the OCEAN repository root, run the full collection, validation, and plot
pipeline with a unique run ID:

```bash
python3 script/reproduce_figures_6_9.py all \
  --fig 8 \
  --config script/figures_6_9/config.example.toml \
  --run-id figure8-live
```

The default config writes raw logs, `normalized/fig8.csv`, `manifest.json`, and
Figure 8 PDF/PNG plots below `artifact/figures_6_9/figure8-live/`. The runner
fails when a selected adapter or measurement input is absent and never fills
missing cells from paper-digitized, historical, or synthetic values.

## Limitations

- Currently supports OpenMPI; may need adjustments for MPICH or Intel MPI
- Memory is allocated sequentially; no defragmentation
- Some advanced MPI features may bypass the shim layer
- Maximum mapping table size is fixed (65536 entries)

## Future Enhancements

- [ ] Support for memory pools and custom allocators
- [ ] Integration with GROMACS-specific memory management
- [ ] Performance profiling and statistics collection
- [ ] Dynamic memory defragmentation
- [ ] Support for persistent memory semantics
