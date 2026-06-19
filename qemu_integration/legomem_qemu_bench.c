#include "qemu_legomem.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static uint64_t now_ns(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static void usage(const char *prog)
{
    printf("usage: %s [host] [port] [region_id] [iterations] [block_size]\n",
           prog);
    printf("defaults: 127.0.0.1 9999 1 10000 64\n");
}

static int parse_u64(const char *text, uint64_t *out)
{
    char *end = NULL;
    unsigned long long value;

    errno = 0;
    value = strtoull(text, &end, 0);
    if (errno || !end || *end != '\0') {
        return -1;
    }

    *out = (uint64_t)value;
    return 0;
}

static void fill_pattern(uint8_t *buf, size_t len, uint64_t iter)
{
    for (size_t i = 0; i < len; ++i) {
        buf[i] = (uint8_t)((iter + i * 131U) & 0xffU);
    }
}

int main(int argc, char **argv)
{
    const char *host = "127.0.0.1";
    uint64_t port = 9999;
    uint64_t region_id = LEGOMEM_QEMU_DEFAULT_REGION_ID;
    uint64_t iterations = 10000;
    uint64_t block_size = LEGOMEM_QEMU_CACHELINE_SIZE;
    uint64_t region_span = 8ULL * 1024ULL * 1024ULL;
    LegoMemQemuClient client;
    uint8_t *write_buf = NULL;
    uint8_t *read_buf = NULL;
    uint64_t start_ns;
    uint64_t elapsed_ns;
    uint64_t protocol_ops;
    double seconds;
    double mib;
    int rc = 1;

    if (argc > 1 &&
        (!strcmp(argv[1], "-h") || !strcmp(argv[1], "--help"))) {
        usage(argv[0]);
        return 0;
    }

    if (argc > 1) {
        host = argv[1];
    }
    if (argc > 2 && parse_u64(argv[2], &port) < 0) {
        fprintf(stderr, "invalid port: %s\n", argv[2]);
        return 2;
    }
    if (argc > 3 && parse_u64(argv[3], &region_id) < 0) {
        fprintf(stderr, "invalid region_id: %s\n", argv[3]);
        return 2;
    }
    if (argc > 4 && parse_u64(argv[4], &iterations) < 0) {
        fprintf(stderr, "invalid iterations: %s\n", argv[4]);
        return 2;
    }
    if (argc > 5 && parse_u64(argv[5], &block_size) < 0) {
        fprintf(stderr, "invalid block_size: %s\n", argv[5]);
        return 2;
    }
    if (argc > 6) {
        usage(argv[0]);
        return 2;
    }

    if (port > UINT16_MAX || iterations == 0 || block_size == 0 ||
        block_size > 1024ULL * 1024ULL) {
        fprintf(stderr, "invalid benchmark range\n");
        return 2;
    }

    if (block_size > region_span) {
        region_span = block_size;
    }

    write_buf = malloc((size_t)block_size);
    read_buf = malloc((size_t)block_size);
    if (!write_buf || !read_buf) {
        fprintf(stderr, "failed to allocate benchmark buffers\n");
        goto out;
    }

    if (legomem_qemu_client_init(&client, host, (int)port, region_id) < 0) {
        fprintf(stderr, "failed to connect to LegoMem server at %s:%llu\n",
                host, (unsigned long long)port);
        goto out;
    }

    start_ns = now_ns();
    for (uint64_t iter = 0; iter < iterations; ++iter) {
        uint64_t offset = (iter * block_size) % region_span;

        fill_pattern(write_buf, (size_t)block_size, iter);
        memset(read_buf, 0, (size_t)block_size);

        if (legomem_qemu_write(&client, region_id, offset, write_buf,
                               (unsigned)block_size) < 0) {
            fprintf(stderr, "write failed at iteration %llu\n",
                    (unsigned long long)iter);
            goto close_client;
        }
        if (legomem_qemu_read(&client, region_id, offset, read_buf,
                              (unsigned)block_size) < 0) {
            fprintf(stderr, "read failed at iteration %llu\n",
                    (unsigned long long)iter);
            goto close_client;
        }
        if (memcmp(write_buf, read_buf, (size_t)block_size) != 0) {
            fprintf(stderr, "verification failed at iteration %llu\n",
                    (unsigned long long)iter);
            goto close_client;
        }
    }
    elapsed_ns = now_ns() - start_ns;

    seconds = (double)elapsed_ns / 1000000000.0;
    mib = (double)(iterations * block_size * 2ULL) / (1024.0 * 1024.0);
    protocol_ops = iterations * 2ULL *
                   ((block_size + LEGOMEM_QEMU_CACHELINE_SIZE - 1ULL) /
                    LEGOMEM_QEMU_CACHELINE_SIZE);

    printf("host=%s port=%llu region_id=%llu iterations=%llu block_size=%llu\n",
           host, (unsigned long long)port, (unsigned long long)region_id,
           (unsigned long long)iterations, (unsigned long long)block_size);
    printf("logical_ops=%llu protocol_ops=%llu bytes=%llu seconds=%.6f\n",
           (unsigned long long)(iterations * 2ULL),
           (unsigned long long)protocol_ops,
           (unsigned long long)(iterations * block_size * 2ULL), seconds);
    printf("latency_ns_per_logical_op=%.2f throughput_mib_s=%.2f\n",
           (double)elapsed_ns / (double)(iterations * 2ULL), mib / seconds);

    rc = 0;

close_client:
    legomem_qemu_client_close(&client);
out:
    free(write_buf);
    free(read_buf);
    return rc;
}
