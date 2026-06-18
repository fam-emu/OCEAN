# QEMU LegoMem Direct Integration

This directory contains the QEMU-facing LegoMem integration. LegoMem is treated as a memory server. QEMU support is provided by a direct C library that QEMU code can link and call, plus a vendored QEMU `memory-backend-legomem` object for NUMA placement. There is no preload path and no device-model dependency.

## Build

```bash
cd qemu_integration
cmake -S . -B build
cmake --build build -j
ctest --test-dir build --output-on-failure
```

Build outputs:

- `libqemu_legomem.a`: direct QEMU integration library.
- `legomem_server`: simple region-addressed memory server.
- `test_qemu_legomem_api`: API smoke test.

## Direct QEMU Contract

QEMU code should include:

```c
#include "qemu_legomem.h"
```

Then initialize one client per memory backend or address space:

```c
LegoMemQemuClient client;
legomem_qemu_client_init(&client, "127.0.0.1", 9999, 1);
```

Memory backend code can forward operations to the server:

```c
legomem_qemu_read(&client, region_id, offset, buf, len);
legomem_qemu_write(&client, region_id, offset, buf, len);
legomem_qemu_fence(&client, region_id);
legomem_qemu_flush(&client, region_id, offset, len);
```

The address visible to the LegoMem server is:

```text
<region_id, offset>
```

## NUMA Launch Path

`launch_qemu_legomem.sh` launches patched QEMU with a LegoMem NUMA node:

```bash
export LEGOMEM_SERVER_HOST=127.0.0.1
export LEGOMEM_SERVER_PORT=9999
export LEGOMEM_REGION_ID=1
./launch_qemu_legomem.sh
```

The launcher uses:

```text
-object memory-backend-legomem,id=legomem-node1,size=...,server=...,port=...,region-id=...
-numa node,nodeid=1,memdev=legomem-node1
```

The default QEMU binary is `../library/qemu/build/qemu-system-x86_64` when that patched build exists, otherwise `/usr/local/bin/qemu-system-x86_64`.

## Server

Start the memory server:

```bash
cd qemu_integration/build
./legomem_server 9999
```

The server stores bytes by `region_id:offset` and supports read, write, fence, and flush request types.
