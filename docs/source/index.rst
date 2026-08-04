OCEAN Documentation
===================

**Open-Source CXL 3.0 Emulator for Datacenter Research**

CXL 3.0 introduces memory pooling capabilities that promise to transform datacenter 
architectures, but the lack of available hardware limits research and development. 
OCEAN bridges this gap with a comprehensive software emulation framework that enables 
full CXL 3.0 functionality before physical hardware deployment.

What is OCEAN?
--------------

OCEAN emulates complete CXL 3.0 memory systems including:

* **Multi-host memory pooling** — Share memory resources across virtual machines
* **Fabric management** — Distributed fabric manager with configurable switch topologies
* **Dynamic capacity allocation** — Runtime memory allocation and deallocation
* **Cache coherence** — Hardware-based consistency with HITM tracking
* **Unmodified workloads** — Run existing applications without code changes

**Performance:** Within approximately 3x of projected native CXL 3.0 speeds with full 
software stack compatibility.

**Validated with real workloads:** Molecular dynamics (GROMACS), distributed databases 
(TIGON), and LLMs demonstrate up to 15% performance improvement over RDMA-based approaches.


Key Capabilities
----------------

* **CXL 3.0 Protocol** — Memory pooling, switch emulation, coherence, dynamic capacity
* **Multi-Host Architecture** — Tested with 2+ hosts for realistic scenarios
* **Workload Support** — Scientific simulations, databases, ML workloads, MPI benchmarks
* **Detailed Metrics** — Application, VM, and fabric-level instrumentation

For architecture details, see :doc:`architecture`.

Use Cases
---------

* **Scientific Computing** — MPI simulations on pooled CXL memory
* **Distributed Systems** — Database performance with disaggregated memory
* **Machine Learning** — LLM workloads with dynamic memory allocation
* **System Software** — Develop and test CXL-aware allocators and policies

For examples and results, see :doc:`workloads`.

Quick Start
-----------

.. code-block:: bash

   git clone https://github.com/cxl-emu/OCEAN.git
   cd OCEAN
   bash ./script/setup_host.sh
   bash ./script/setup_network.sh 2

For step-by-step instructions, see :doc:`getting-started`.

Documentation Structure
-----------------------

* :doc:`getting-started` — Installation and initial experiments
* :doc:`architecture` — System components and design
* :doc:`slugarch-legomem` — SlugArch replay model for LegoMem
* :doc:`configuration` — Build and runtime options
* :doc:`workloads` — Running experiments and analyzing results
* :doc:`reproducing-figures-6-9` — Collecting and plotting paper Figures 6--9

.. toctree::
   :maxdepth: 1
   :hidden:

   getting-started
   architecture
   slugarch-legomem
   configuration
   workloads
   reproducing-figures-6-9

Community
---------

* **Project Website**: https://cxl-emu.github.io
* **GitHub Repository**: https://github.com/cxl-emu/OCEAN
