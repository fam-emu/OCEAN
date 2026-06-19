==========================
SlugArch for LegoMem
==========================

Design
======

Architectural Model
-------------------

LegoMem models a heterogeneous system as a set of Von Neumann islands connected
through a replayable memory-server boundary. An island may be a CPU core
complex, GPU runtime, DPU, simulator, compression engine, QEMU guest, or other
endpoint that issues memory-server-visible operations. Internally, an island
may execute arbitrary instructions, firmware, microcode, or hardware state
machines. Externally, it must expose a SlugArch-compatible boundary model.

The SlugArch boundary model is not only a load/store datapath. It is a
programmable boundary contract that observes and optionally transforms:

* memory reads and writes;
* ownership transitions, invalidations, and flushes;
* DMA and peer-to-peer transfers;
* offload descriptors, doorbells, completions, and interrupts;
* address translation and page migration events;
* security labels, protection keys, and provenance tags.

SlugArch does not need to understand the full private ISA of an endpoint. It
only needs to observe externally visible memory and offload behavior, plus
enough local timing information to divide execution into replayable epochs.

Assumptions and Non-Goals
-------------------------

SlugArch is a boundary contract for LegoMem, not a new universal accelerator
ISA. It assumes participating endpoints cannot bypass the LegoMem boundary when
issuing covered transactions, that the operating system, hypervisor, or runtime
controls installation of SlugArch policies, and that those policies are bounded,
verifiable, and revocable. It also assumes validation-mode commitments use
collision-resistant hashes, and that replay endpoints implement the same
boundary-event contract as the recording endpoints.

SlugArch does not aim to reproduce cycle timing, private warp scheduling,
replacement decisions, or other internal microarchitectural behavior. It also
does not make an arbitrary nondeterministic endpoint deterministic by fiat. If a
timer, MMIO read, failure, network packet, or device-private computation can
affect a covered boundary event, the relevant value must be recorded in full,
delta, or validation mode.

The guarantee is fail-stop replay for the objects named by a coverage policy:
replay either reconstructs the same memory-server-visible behavior or reports
the first boundary mismatch.

Policy JIT
----------

The SlugArch policy JIT compiles high-level replay and security policies into
small endpoint-local or server-local programs that run near the LegoMem
boundary. A fixed tracer must either record too much or expose too little. A
pure software tracer can be too late and too slow. The policy JIT occupies the
middle ground: it specializes boundary observation for the current workload and
overhead budget.

The input to the policy JIT is a policy bundle:

* the current topology of QEMU guests, runtimes, accelerators, memory-server
  processes, and simulator endpoints;
* page tables, protection domains, and shared-memory regions;
* workload hints such as kernel launch boundaries, queue identifiers, or tensor
  allocation ranges;
* replay goals such as deterministic debugging, migration, intrusion detection,
  or fault recovery;
* overhead budgets such as maximum metadata bandwidth or sampling rate.

The output is a set of bounded programs installed into LegoMem boundary points.
These programs classify transactions, attach epoch identifiers, filter
uninteresting traffic, compress repeated patterns, and emit replay records. A
useful mental model is an eBPF-like system for a memory server boundary, except
the target is the LegoMem data path rather than the operating-system kernel.

Replay Records
--------------

SlugArch records only the information needed to reconstruct
memory-server-visible behavior. A replay record contains:

* a source endpoint and destination endpoint;
* an epoch identifier;
* the transaction class, such as ``LM_READ``, ``LM_WRITE``, ``LM_ATOMIC``,
  ``LM_FLUSH``, ``LM_FENCE``, invalidation, doorbell, completion, or interrupt;
* an address, range, queue object, or migration object;
* ordering metadata, including dependencies and fences;
* a payload, payload delta, or payload commitment depending on policy;
* optional provenance and security labels.

The model supports four recording modes:

* ``Full`` stores the payload and checks exact replay equality.
* ``Delta`` stores a deterministic transform from the checkpointed value.
* ``Validation`` stores a commitment and validates re-execution output.
* ``OrderingOnly`` stores dependencies and ordering metadata without payload.

The current C++ model uses a stable lightweight digest so tests are
deterministic across machines. A production deployment can replace that helper
with a stronger commitment function without changing the record-mode API.

LegoMem Boundary Replay
-----------------------

SlugArch replay reconstructs the same externally visible LegoMem behavior. It
does not require reimplementing a GPU scheduler or decoding every accelerator
instruction. Instead, it treats each endpoint as a black box between epochs and
forces equivalence at the LegoMem boundary.

Replay proceeds in four phases:

1. **Checkpoint.** Capture selected endpoint state, memory-server mappings, and
   SlugArch policy versions.
2. **Record.** SlugArch policies emit compressed transaction records and
   dependency edges while endpoints run normally.
3. **Seal.** At epoch boundaries, LegoMem records a compact commitment over
   memory ranges, queue states, outstanding ownership, and protection labels.
4. **Reconstruct.** On the replay machine, LegoMem reinstalls compatible
   SlugArch policies, replays or validates boundary events, and stalls endpoints
   when they attempt to cross an epoch boundary before dependencies are
   satisfied.

The key idea is that replay does not need to preserve every cycle. It needs to
preserve the happens-before relation that affects memory-visible behavior. If a
GPU write, a CPU read, and a memory-server ordering decision are recorded with
the necessary dependency edges, then a replay run can reproduce the same
program-visible state even on a different endpoint or memory-server instance.

Epochs and Stalls
-----------------

SlugArch uses deterministic epochs to control replay granularity. An epoch can
be defined by a kernel launch, a queue submission, a page-fault batch, a timer
interrupt, a memory fence, or a policy-inserted synthetic boundary. When an
endpoint reaches an epoch boundary during replay, its boundary program may
stall outgoing requests until the required incoming LegoMem events have been
replayed.

This resembles inserting memory-operation bubbles into a compiled execution.
The processor or accelerator does not need to stop forever; it only waits when
the replay contract requires causality to catch up. Without ISA support, these
waits can be approximated through QEMU backend throttling, queue throttling,
page protection, server-side retry, or device-firmware cooperation.

Security and Observability
--------------------------

The same replay plane also supports security. A malicious or faulty endpoint may
issue legal memory-server requests that violate a higher-level policy. Because
SlugArch observes transactions at the LegoMem boundary, it can enforce
provenance rules such as: this GPU kernel may write only these pages; this DMA
engine may read only descriptors signed by this CPU protection domain; this
memory-server region may not serve stale data after an ownership transition.

SlugArch allows these checks to be specialized. During normal execution, the
boundary can emit compact provenance commitments. During incident response, it
can switch to full recording for suspicious regions. During replay, the system
can identify whether memory corruption came from CPU code, accelerator DMA,
server-side ordering, or external input.

Compatibility Path
------------------

SlugArch does not require replacing all existing software. It can begin as an
opt-in mode for LegoMem and QEMU:

* CPUs use existing tracing support for local control-flow reconstruction.
* GPU runtimes expose kernel, queue, and allocation boundaries as policy hints.
* QEMU memory backends expose guest memory operations to the LegoMem server.
* The LegoMem server records ordered memory transactions for selected regions.
* Operating systems and runtimes manage SlugArch policies similarly to how they
  manage IOMMUs, performance counters, device firmware, and accelerator queues.

Over time, the replay contract can move closer to the endpoint boundary, but the
core LegoMem interface remains a memory-server protocol with explicit replay
records, epochs, seals, and policy-controlled recording modes.
