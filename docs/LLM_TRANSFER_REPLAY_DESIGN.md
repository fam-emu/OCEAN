# LLM tensor/KV bulk-transfer replay

## Scope and provenance

`llm_transfer_replay` is a standalone modeled-performance frontend for
OCEAN/CXLMemSim. It consumes versioned tensor/KV-block transfer requests and
produces event-level service completions. It does not require QEMU, the OCEAN
TCP server, a CPU instruction trace, a GPU, or root privileges.

The output is `modeled`. QEMU Type-3/devdax results remain `functional`, and
H100/NUMA observations remain `measured`. These labels are not interchangeable.

## Audit of the existing OCEAN path

| Component | Existing API / behavior | Reuse decision |
|---|---|---|
| `src/main_server.cc` | Constructs `CXLController`, accepts QEMU/TCP requests, then adds server-side congestion, coherency and a fixed transfer term | Keep for Phase-1 functional regression; exclude from the LLM performance path to avoid TCP wall time and double counting |
| `include/cxlcontroller.h`, `src/cxlcontroller.cpp` | `construct_topo()`, `calculate_latency()`, `calculate_bandwidth()` over access tuples | Topology concepts are reusable; the tuple API is cache-line/CPU-trace oriented |
| `include/cxlendpoint.h`, `src/cxlendpoint.cpp` | `CXLMemExpander`, `CXLSwitch`, `RemoteCXLExpander`, `FabricLink`; cache-line latency, bandwidth windows and conflict congestion | Parameters and topology concepts are reused; the old unit/traffic semantics are not called by the bulk frontend |
| `include/hdm_decoder.h`, `src/hdm_decoder.cpp` | Range, interleaved and hybrid address decoding | Reusable design for future multi-expander mapping; Step 1 uses deterministic address-to-expander striping |
| `include/rob.h`, `src/rob.cpp`, `src/rob.cc` | Parses CPU/O3PipeView instruction events and advances a reorder buffer | Not used: a GPU tensor demand is not a CPU load instruction and a GiB tensor must not become millions of ROB entries |
| cache/migration/coherency code | Models CPU cache and page/coherency behaviors | Disabled in the initial read-mostly LLM bulk model; may become an explicit optional model later |

The old endpoint tuple is treated as `(timestamp, address)` by endpoint code,
while some server call sites construct values from address/size. The standalone
frontend therefore does not pass ambiguous tuples through that interface.
All new units are explicit: time in nanoseconds, sizes in bytes, and bandwidth
in GiB/s.

## Architecture and call path

```text
transfer-events.csv
    -> loadTransferRequestsCsv()
    -> TensorTransferModel::replay()
       -> address-to-expander mapping
       -> direct/staged service equation
       -> per-expander/per-port FIFO and transfer dependencies
    -> ocean-service-events.csv
    -> writeReplayMetadataJson()
```

The public types are in `include/llm_transfer_model.h`; parsing and modeling are
in `src/llm_transfer_model.cpp`; the CLI is `src/llm_transfer_replay.cpp`.
`script/build_llm_transfer_replay.sh` builds only this path with a C++20
compiler, so unrelated QEMU/server dependencies cannot affect synthetic replay.

## Service model

For a direct DMA path:

```text
B_effective = min(B_CXL_read_or_write, B_GPU_link)
T_service = T_base + T_media + hops * T_hop + bytes_rounded / B_effective
```

For a staged path:

```text
T_service = T_base + T_media + hops * T_hop + T_local_to_gpu
          + bytes_rounded / min(B_CXL, B_local_DRAM)
          + bytes_rounded / min(B_local_DRAM, B_GPU_link)
```

`service_start` is the maximum of issue time, declared transfer-dependency
completion, and the selected port's FIFO-ready time. `queue_delay` includes
both dependency and port waiting relative to issue time. The model charges one
fixed latency per bulk request and serializes its bandwidth time on one port.
Different expander/port lanes may run concurrently. Each port currently receives
the configured endpoint bandwidth; a shared upstream-switch bandwidth pool is a
future extension.

The current `congestion_model` values are `fifo` and `none`. FIFO queue delay is
the contention term, so `congestion_delay_ns` remains zero; adding a second
penalty would double count. `queue_depth` and `max_outstanding_requests` are
recorded configuration constraints. `max_outstanding_requests` bounds modeled
lanes; the model does not drop or back-pressure a trace whose producer exceeds
`queue_depth`.

## Aggregation and detailed-mode boundary

Requests remain tensor, weight-group, chunk, KV-block/page, adapter, expert, or
synthetic bulk events. A request is rounded to `transfer_granularity_bytes`, but
is never unconditionally expanded to 64-byte records.

`aggregate` and `detailed` currently evaluate the same closed-form equation;
`auto` selects the label using `detailed_threshold_bytes`. Thus “detailed” means
the granularity-aware analytical path, not cycle-accurate cache-line simulation.
Their numerical difference is exactly zero for the same rounded size. The
remaining abstraction error—per-packet arbitration, credit return, shared-link
contention, protocol overhead, and burst effects—must be quantified against
microbenchmarks before hardware claims. The output assumptions explicitly say
`analytical_granularity_fast_path`.

## Versioned interfaces

The standalone binary accepts CSV schema version 1. The surrounding
`cxl-llm-step1` adapter owns Parquet/JSONL conversion.

Transfer input contains:

- identity: `schema_version`, `event_id`, `request_id`, `object_id`;
- semantics: `object_type`, `data_type`, `phase`, `layer_id`, `direction`;
- timing: `issue_time_ns`, `need_time_ns`, `can_overlap`, `dependency_ids`;
- placement: `logical_address`, `source_tier`, `destination_tier`;
- traffic/queue: `bytes`, `client_id`, `queue_id`, `queue_depth`,
  `transfer_granularity_bytes`;
- trust: optional `provenance`.

Service output contains the required timing decomposition plus `direction`:
`event_id`, `endpoint_id`, `issue_time_ns`, `service_start_ns`,
`service_end_ns`, `queue_delay_ns`, `base_latency_ns`, `media_latency_ns`,
`topology_latency_ns`, `bandwidth_delay_ns`, `congestion_delay_ns`,
`total_service_time_ns`, effective bandwidth, requested/modeled bytes,
`capacity_hit`, mode, assumptions, and provenance.

The hardware YAML supports direct/staged path, latency components, read/write,
GPU-link and local-DRAM bandwidth, capacity, expander count, switch hops,
per-hop latency, ports, clients, FIFO settings, outstanding requests, transfer
granularity, and the analytical-mode threshold. Unknown nested YAML structures
are not supported by the dependency-free parser.

## Build and synthetic replay

```bash
bash script/build_llm_transfer_replay.sh build-llm-replay

build-llm-replay/llm_transfer_replay \
  --trace examples/llm_transfer_replay/transfer-events.csv \
  --hardware-profile examples/llm_transfer_replay/ocean-hardware-profile.yaml \
  --output /tmp/ocean-service-events.csv \
  --metadata-output /tmp/ocean-service-events.metadata.json

python3 -m unittest discover -s tests -p 'test_llm_transfer_replay.py' -v
```

## QEMU role and limitations

QEMU is retained for CXL Type-3 topology, devdax and software-stack compatibility
regression. It is not in the modeled LLM performance critical path. TCP service
wall time is never reported as GPU CXL latency or bandwidth.

Step-1 limitations are: analytical rather than protocol/cycle accuracy; no
shared-switch bandwidth pool or credit back-pressure; no cache/coherency/migration
effects; no dynamic re-issue after execution-timeline shifts; no calibrated
hardware-CXL profile yet; and no real tensor-to-kernel dependency trace until the
H100 tracing path is repaired and enriched.
