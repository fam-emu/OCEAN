#ifndef QEMU_LEGOMEM_H
#define QEMU_LEGOMEM_H

#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>
#include <pthread.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LEGOMEM_QEMU_CACHELINE_SIZE 64
#define LEGOMEM_QEMU_DEFAULT_REGION_ID 1

#define LEGOMEM_QEMU_OP_READ 0
#define LEGOMEM_QEMU_OP_WRITE 1
#define LEGOMEM_QEMU_OP_FENCE 2
#define LEGOMEM_QEMU_OP_FLUSH 3

#define LEGOMEM_QEMU_STATUS_OK 0
#define LEGOMEM_QEMU_STATUS_ERR 1

typedef struct {
    char host[256];
    int port;
    int socket_fd;
    bool connected;
    uint64_t default_region_id;
    uint64_t total_reads;
    uint64_t total_writes;
    pthread_mutex_t lock;
} LegoMemQemuClient;

typedef struct {
    uint8_t op_type;
    uint64_t region_id;
    uint64_t offset;
    uint64_t size;
    uint64_t timestamp;
    uint8_t data[LEGOMEM_QEMU_CACHELINE_SIZE];
} LegoMemQemuRequest;

typedef struct {
    uint8_t status;
    uint64_t latency_ns;
    uint8_t data[LEGOMEM_QEMU_CACHELINE_SIZE];
} LegoMemQemuResponse;

int legomem_qemu_client_init(LegoMemQemuClient *client, const char *host, int port, uint64_t default_region_id);
void legomem_qemu_client_close(LegoMemQemuClient *client);

int legomem_qemu_read(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, void *data, unsigned size);
int legomem_qemu_write(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, const void *data, unsigned size);
int legomem_qemu_fence(LegoMemQemuClient *client, uint64_t region_id);
int legomem_qemu_flush(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, unsigned size);

#ifdef __cplusplus
}
#endif

#endif
