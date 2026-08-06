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
| `include/cxlcontroller.h`, `src/cxlcontroller.cpp` | `construct_topo()`, `insert()`, `calculate_latency()`, `calculate_bandwidth()` over access tuples | The legacy entry is CPU/ROB and Linux-server coupled. The dependency-light `CXLMemSimBulkController::service()` is the explicit bulk controller entry. |
| `include/cxlendpoint.h`, `src/cxlendpoint.cpp` | `CXLMemExpander`, `CXLSwitch`, `RemoteCXLExpander`, `FabricLink`; cache-line latency, bandwidth windows and conflict congestion | The old endpoint remains intact. `CXLMemSimBulkExpander::serviceChunk()` is the explicit byte/ns service method with per-port FIFO state. |
| `include/hdm_decoder.h`, `src/hdm_decoder.cpp` | Range, interleaved and hybrid address decoding | Directly reused by the core backend: every modeled chunk calls `HDMDecoder::decode()`. |
| `include/rob.h`, `src/rob.cpp`, `src/rob.cc` | Parses CPU/O3PipeView instruction events and advances a reorder buffer | Not used: a GPU tensor demand is not a CPU load instruction and a GiB tensor must not become millions of ROB entries |
| cache/migration/coherency code | Models CPU cache and page/coherency behaviors | Disabled in the initial read-mostly LLM bulk model; may become an explicit optional model later |

The old endpoint tuple is treated as `(timestamp, address)` by
`CXLMemExpander::calculate_latency()` and `calculate_bandwidth()`, while some
server call sites construct values from address/size. `calculate_bandwidth()`
also assumes 64-byte accesses in a fixed 20 ms window. The controller's
`insert()` path advances CPU ROB indices, invokes allocation/paging/cache
policies, and contains a process-global static request counter. The server then
adds its own transfer and congestion terms. Calling that path for a bulk event
would therefore be ambiguous, non-reentrant, potentially double counted, and
would turn a GiB object into millions of CPU records.

The new core path extracts the reusable controller/decoder/expander boundary
into dependency-light classes rather than treating the legacy tuple as a bulk
API. It has no global mutable state, exposes completion timestamps directly,
and uses explicit nanoseconds, bytes, and GiB/s throughout.

## Architecture and call path

```text
transfer-events.csv
    -> loadTransferRequestsCsv()
    -> TensorTransferModel::replay()
       -> selected backend
          -> analytical closed-form reference, or
          -> CXLMemSimBulkController::service()
             -> HDMDecoder::decode()
             -> CXLMemSimBulkExpander::serviceChunk()
             -> per-expander/per-port FIFO
    -> ocean-service-events.csv
    -> writeReplayMetadataJson()
```

The frontend types are in `include/llm_transfer_model.h`. The explicit core
request/completion types and controller/expander API are in
`include/llm_bulk_core.h`, with implementation in `src/llm_bulk_core.cpp`.
Parsing and analytical reference modeling remain in
`src/llm_transfer_model.cpp`; the CLI is `src/llm_transfer_replay.cpp`.
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

For the analytical backend, `service_start` is the maximum of issue time,
declared transfer-dependency completion, and the selected port's FIFO-ready
time. It charges one fixed latency per bulk request. For the core backend, the
controller splits a `detailed` request into configured chunks, routes every
chunk through `HDMDecoder`, and calls the selected bulk expander's service
method. The parent completion is reconstructed from its completion-critical
chunk and labeled `critical_chunk_decomposition`. `aggregate` sends one parent
chunk, while `auto` avoids per-page expansion above the detailed threshold.
Different expander/port lanes may run concurrently.

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

For `--backend analytical`, `aggregate` and `detailed` intentionally evaluate
the same deterministic reference equation. For `--backend cxlmemsim-core`,
`aggregate` is one bulk service call and `detailed` exercises chunk routing and
port arbitration. Neither mode is cycle accurate. The remaining abstraction
error—credit return, shared-link contention, protocol overhead, and calibration
error—must be quantified against microbenchmarks before hardware claims.

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
`chunk_count`, `capacity_hit`, backend, mode, assumptions, and provenance.

Metadata records backend selection, the exact implementation method path,
FNV-1a content fingerprints for the hardware profile and input trace, effective
topology, transfer granularity, and controller/decoder/expander call counters.
A core test requires all three counters to be positive. Backend initialization
or routing failures are fatal; the CLI never silently falls back.

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
  --metadata-output /tmp/ocean-service-events.metadata.json \
  --backend cxlmemsim-core

python3 -m unittest discover -s tests -p 'test_llm_transfer_replay.py' -v
```

## QEMU role and limitations

QEMU is retained for CXL Type-3 topology, devdax and software-stack compatibility
regression. It is not in the modeled LLM performance critical path. TCP service
wall time is never reported as GPU CXL latency or bandwidth.

Current limitations are: neither backend is protocol/cycle accurate; no shared-
switch bandwidth pool or credit back-pressure; no cache/coherency effects in
the offline bulk path; parent decomposition follows the completion-critical
chunk; no dynamic re-issue after execution-timeline shifts; no calibrated
hardware-CXL profile yet; and no real tensor-to-kernel dependency trace until
the H100 tracing path is repaired and enriched.
