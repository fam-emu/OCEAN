#include "qemu/osdep.h"
#include "qemu/log.h"
#include "hw/cxl/cxl_memsim_browser.h"

#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#endif

#define CXL_BROWSER_OP_READ       0
#define CXL_BROWSER_OP_WRITE      1
#define CXL_BROWSER_OP_FAA        3
#define CXL_BROWSER_OP_CAS        4
#define CXL_BROWSER_OP_FENCE      5
#define CXL_BROWSER_OP_LSA_READ   6
#define CXL_BROWSER_OP_LSA_WRITE  7

#define CXL_BROWSER_MAX_DATA 64

typedef struct QEMU_PACKED CXLBrowserResponse {
    uint8_t status;
    uint64_t latency_ns;
    uint64_t old_value;
    uint8_t data[CXL_BROWSER_MAX_DATA];
} CXLBrowserResponse;

bool cxl_memsim_transport_is_browser(const char *transport)
{
    return transport &&
           (g_strcmp0(transport, "browser") == 0 ||
            g_strcmp0(transport, "sharedworker") == 0 ||
            g_strcmp0(transport, "wasm") == 0 ||
            g_strcmp0(transport, "wasm-shared") == 0);
}

#ifdef __EMSCRIPTEN__

static inline uint32_t u64_lo(uint64_t value)
{
    return (uint32_t)(value & 0xffffffffu);
}

static inline uint32_t u64_hi(uint64_t value)
{
    return (uint32_t)(value >> 32);
}

EM_JS(int, cxl_browser_connect_js,
      (const char *device_ptr, const char *pool_ptr, int port,
       uint32_t size_lo, uint32_t size_hi), {
    function text(ptr, fallback) {
        return ptr ? UTF8ToString(ptr) : fallback;
    }
    function env(name, fallback) {
        if (typeof Module !== 'undefined') {
            if (Module['ENV'] && Module['ENV'][name]) {
                return Module['ENV'][name];
            }
            if (Module[name]) {
                return Module[name];
            }
        }
        return fallback;
    }

    const argDevice = text(device_ptr, 'qemu');
    const device = env('CXL_MEMSIM_DEVICE', argDevice) || argDevice;
    const pool = env('CXL_MEMSIM_POOL', text(pool_ptr, 'CXLMemSim')) || 'CXLMemSim';
    const size = Number(size_lo >>> 0) + Number(size_hi >>> 0) * 4294967296;
    const root = globalThis;
    const state = root.__HETGPU_CXL_BROWSER_MEMSIM ||
        (root.__HETGPU_CXL_BROWSER_MEMSIM = {
            connected: false,
            queue: [],
            clientId: env('CXL_MEMSIM_CLIENT_ID',
                'qemu-' + Math.random().toString(16).slice(2)),
            pool: pool,
            device: device,
            port: null,
            worker: null,
            requestSab: null
        });

    state.pool = pool;
    state.device = device;
    state.poolSize = size || Number(env('CXL_MEMSIM_SIZE', 268435456));
    state.clientId = env('CXL_MEMSIM_CLIENT_ID', state.clientId) || state.clientId;
    if (state.connected && state.port) {
        return 0;
    }
    if (typeof SharedWorker === 'undefined' ||
        typeof SharedArrayBuffer === 'undefined' ||
        typeof Atomics === 'undefined') {
        console.error('CXLMemSim browser transport requires SharedWorker and SharedArrayBuffer');
        return -1;
    }

    let workerUrl = env('CXL_MEMSIM_WORKER_URL',
        env('HETGPU_CXL_MEMSIM_WORKER_URL', null));
    if (!workerUrl) {
        const base = root.location && root.location.href ? root.location.href : 'http://127.0.0.1/';
        workerUrl = new URL('/cxl2/cxlmemsim-pool-worker.js', base).href;
    }
    const workerName = env('CXL_MEMSIM_WORKER_NAME', 'hetgpu-cxlmemsim');

    try {
        state.worker = new SharedWorker(workerUrl, workerName);
        state.port = state.worker.port;
        state.port.onmessage = (event) => {
            const msg = event.data || {};
            if (msg.type === 'message' && msg.bytes) {
                state.queue.push(new Uint8Array(msg.bytes));
            } else if (msg.type === 'connected' && msg.clientId) {
                state.clientId = msg.clientId;
            } else if (msg.type === 'error') {
                console.error('CXLMemSim worker:', msg.message || msg);
            }
        };
        state.port.start();
        state.port.postMessage({
            type: 'connect',
            role: 'qemu',
            clientId: state.clientId,
            device,
            pool,
            port,
            size: state.poolSize || 268435456
        });
        state.connected = true;
        return 0;
    } catch (error) {
        console.error('Failed to connect CXLMemSim SharedWorker:', error);
        state.connected = false;
        state.port = null;
        return -1;
    }
});

EM_JS(void, cxl_browser_disconnect_js, (const char *device_ptr), {
    const state = globalThis.__HETGPU_CXL_BROWSER_MEMSIM;
    if (!state || !state.port) {
        return;
    }
    const device = device_ptr ? UTF8ToString(device_ptr) : state.device;
    state.port.postMessage({
        type: 'disconnect',
        clientId: state.clientId,
        device: device || state.device,
        pool: state.pool
    });
});

EM_JS(int, cxl_browser_request_js,
      (int op, uint32_t addr_lo, uint32_t addr_hi, uint32_t size,
       int data_ptr, uint32_t value_lo, uint32_t value_hi,
       uint32_t expected_lo, uint32_t expected_hi, int resp_ptr,
       uint32_t resp_size), {
    const state = globalThis.__HETGPU_CXL_BROWSER_MEMSIM;
    if (!state || !state.connected || !state.port) {
        return -1;
    }
    if (size > 64) {
        return -2;
    }
    if (typeof Atomics.wait !== 'function') {
        return -3;
    }

    const sab = state.requestSab || (state.requestSab = new SharedArrayBuffer(256));
    const control = new Int32Array(sab, 0, 1);
    const bytes = new Uint8Array(sab);
    bytes.fill(0);
    Atomics.store(control, 0, 0);

    if ((op === 1 || op === 7) && data_ptr && size) {
        bytes.set(HEAPU8.subarray(data_ptr, data_ptr + size), 64);
    }

    state.port.postMessage({
        type: 'sync-request',
        clientId: state.clientId,
        device: state.device,
        pool: state.pool,
        sab,
        op,
        addrLo: addr_lo >>> 0,
        addrHi: addr_hi >>> 0,
        size: size >>> 0,
        valueLo: value_lo >>> 0,
        valueHi: value_hi >>> 0,
        expectedLo: expected_lo >>> 0,
        expectedHi: expected_hi >>> 0
    });

    let waitResult = 'not-equal';
    try {
        waitResult = Atomics.wait(control, 0, 0, 30000);
    } catch (error) {
        console.error('CXLMemSim browser transport cannot block on this thread:', error);
        return -6;
    }
    if (waitResult === 'timed-out') {
        return -4;
    }
    if (Atomics.load(control, 0) < 0) {
        return -5;
    }
    if (resp_ptr && resp_size) {
        HEAPU8.set(bytes.subarray(128, 128 + Math.min(resp_size, 81)), resp_ptr);
    }
    return 0;
});

EM_JS(int, cxl_browser_send_message_js,
      (const char *device_ptr, int data_ptr, uint32_t size), {
    const state = globalThis.__HETGPU_CXL_BROWSER_MEMSIM;
    if (!state || !state.connected || !state.port || !data_ptr || !size) {
        return -1;
    }
    const bytes = HEAPU8.slice(data_ptr, data_ptr + size);
    state.port.postMessage({
        type: 'qemu-message',
        clientId: state.clientId,
        device: device_ptr ? UTF8ToString(device_ptr) : state.device,
        pool: state.pool,
        bytes: bytes.buffer
    }, [bytes.buffer]);
    return 0;
});

EM_JS(int, cxl_browser_recv_message_js,
      (const char *device_ptr, int data_ptr, uint32_t max_size), {
    const state = globalThis.__HETGPU_CXL_BROWSER_MEMSIM;
    if (!state || !state.queue || state.queue.length === 0) {
        return 0;
    }
    const bytes = state.queue.shift();
    if (bytes.length > max_size) {
        return -1;
    }
    HEAPU8.set(bytes, data_ptr);
    return bytes.length;
});

#endif

int cxl_memsim_browser_connect(const char *device, const char *pool,
                               uint16_t port, uint64_t size)
{
#ifdef __EMSCRIPTEN__
    return cxl_browser_connect_js(device, pool, port, u64_lo(size), u64_hi(size));
#else
    qemu_log_mask(LOG_UNIMP,
                  "CXLMemSim browser transport is only available on emscripten\n");
    return -1;
#endif
}

void cxl_memsim_browser_disconnect(const char *device)
{
#ifdef __EMSCRIPTEN__
    cxl_browser_disconnect_js(device);
#else
    (void)device;
#endif
}

int cxl_memsim_browser_request(uint8_t op, uint64_t addr, uint64_t size,
                               const void *data, uint64_t value,
                               uint64_t expected, void *resp,
                               size_t resp_size)
{
#ifdef __EMSCRIPTEN__
    if (size > CXL_BROWSER_MAX_DATA) {
        return -1;
    }
    return cxl_browser_request_js(op, u64_lo(addr), u64_hi(addr),
                                  (uint32_t)size, (int)(uintptr_t)data,
                                  u64_lo(value), u64_hi(value),
                                  u64_lo(expected), u64_hi(expected),
                                  (int)(uintptr_t)resp,
                                  (uint32_t)resp_size);
#else
    (void)op;
    (void)addr;
    (void)size;
    (void)data;
    (void)value;
    (void)expected;
    (void)resp;
    (void)resp_size;
    return -1;
#endif
}

bool cxl_memsim_browser_read(uint64_t addr, void *data, size_t size)
{
    CXLBrowserResponse resp = { 0 };

    if (!data || size > CXL_BROWSER_MAX_DATA) {
        return false;
    }
    if (cxl_memsim_browser_request(CXL_BROWSER_OP_READ, addr, size, NULL,
                                   0, 0, &resp, sizeof(resp)) < 0) {
        return false;
    }
    if (resp.status != 0) {
        return false;
    }
    memcpy(data, resp.data, size);
    return true;
}

bool cxl_memsim_browser_write(uint64_t addr, const void *data, size_t size)
{
    CXLBrowserResponse resp = { 0 };

    if (!data || size > CXL_BROWSER_MAX_DATA) {
        return false;
    }
    if (cxl_memsim_browser_request(CXL_BROWSER_OP_WRITE, addr, size, data,
                                   0, 0, &resp, sizeof(resp)) < 0) {
        return false;
    }
    return resp.status == 0;
}

bool cxl_memsim_browser_send_message(const char *device, const void *data,
                                     size_t size)
{
#ifdef __EMSCRIPTEN__
    if (!data || size > UINT32_MAX) {
        return false;
    }
    return cxl_browser_send_message_js(device, (int)(uintptr_t)data,
                                       (uint32_t)size) == 0;
#else
    (void)device;
    (void)data;
    (void)size;
    return false;
#endif
}

ssize_t cxl_memsim_browser_recv_message(const char *device, void *data,
                                        size_t max_size)
{
#ifdef __EMSCRIPTEN__
    if (!data || max_size > UINT32_MAX) {
        return -1;
    }
    return cxl_browser_recv_message_js(device, (int)(uintptr_t)data,
                                       (uint32_t)max_size);
#else
    (void)device;
    (void)data;
    (void)max_size;
    return -1;
#endif
}
