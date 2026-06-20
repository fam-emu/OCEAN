#include "qemu_legomem.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static uint64_t get_timestamp_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static int send_all(int fd, const void *buf, size_t len) {
    const uint8_t *pos = (const uint8_t *)buf;

    while (len > 0) {
        ssize_t sent = send(fd, pos, len, 0);
        if (sent <= 0) {
            return -1;
        }

        pos += sent;
        len -= (size_t)sent;
    }

    return 0;
}

static int recv_all(int fd, void *buf, size_t len) {
    uint8_t *pos = (uint8_t *)buf;

    while (len > 0) {
        ssize_t received = recv(fd, pos, len, MSG_WAITALL);
        if (received <= 0) {
            return -1;
        }

        pos += received;
        len -= (size_t)received;
    }

    return 0;
}

static int connect_to_server(LegoMemQemuClient *client) {
    struct sockaddr_in server_addr;

    client->socket_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (client->socket_fd < 0) {
        perror("legomem_qemu: socket");
        return -1;
    }

    int nodelay = 1;
    setsockopt(client->socket_fd, IPPROTO_TCP, TCP_NODELAY,
               &nodelay, sizeof(nodelay));

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(client->port);

    if (inet_pton(AF_INET, client->host, &server_addr.sin_addr) <= 0) {
        perror("legomem_qemu: inet_pton");
        close(client->socket_fd);
        client->socket_fd = -1;
        return -1;
    }

    if (connect(client->socket_fd, (struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) {
        perror("legomem_qemu: connect");
        close(client->socket_fd);
        client->socket_fd = -1;
        return -1;
    }

    client->connected = true;
    return 0;
}

static int fail_connection(LegoMemQemuClient *client, const char *operation) {
    perror(operation);
    client->connected = false;
    close(client->socket_fd);
    client->socket_fd = -1;
    return -1;
}

static int send_request(LegoMemQemuClient *client, uint8_t op_type,
                        uint64_t region_id, uint64_t offset,
                        const void *write_data, void *read_data,
                        size_t size) {
    LegoMemQemuRequestHeader req = {0};
    LegoMemQemuResponseHeader resp = {0};

    if (size > LEGOMEM_QEMU_MAX_TRANSFER_SIZE) {
        return -1;
    }

    req.op_type = op_type;
    req.region_id = region_id ? region_id : client->default_region_id;
    req.offset = offset;
    req.size = size;
    req.timestamp = get_timestamp_ns();

    pthread_mutex_lock(&client->lock);

    if (!client->connected && connect_to_server(client) < 0) {
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    if (send_all(client->socket_fd, &req, sizeof(req)) < 0) {
        fail_connection(client, "legomem_qemu: send header");
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    if (op_type == LEGOMEM_QEMU_OP_WRITE && size > 0 &&
        send_all(client->socket_fd, write_data, size) < 0) {
        fail_connection(client, "legomem_qemu: send payload");
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    if (recv_all(client->socket_fd, &resp, sizeof(resp)) < 0) {
        fail_connection(client, "legomem_qemu: recv header");
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    if (resp.status == LEGOMEM_QEMU_STATUS_OK &&
        op_type == LEGOMEM_QEMU_OP_READ && resp.size > 0) {
        if (resp.size > size || recv_all(client->socket_fd, read_data,
                                         (size_t)resp.size) < 0) {
            fail_connection(client, "legomem_qemu: recv payload");
            pthread_mutex_unlock(&client->lock);
            return -1;
        }
    }

    if (resp.status != LEGOMEM_QEMU_STATUS_OK) {
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    pthread_mutex_unlock(&client->lock);
    return 0;
}

int legomem_qemu_client_init(LegoMemQemuClient *client, const char *host, int port, uint64_t default_region_id) {
    if (!client) {
        return -1;
    }

    memset(client, 0, sizeof(*client));
    strncpy(client->host, host ? host : "127.0.0.1", sizeof(client->host) - 1);
    client->port = port > 0 ? port : 9999;
    client->socket_fd = -1;
    client->default_region_id = default_region_id ? default_region_id : LEGOMEM_QEMU_DEFAULT_REGION_ID;
    pthread_mutex_init(&client->lock, NULL);

    return connect_to_server(client);
}

void legomem_qemu_client_close(LegoMemQemuClient *client) {
    if (!client) {
        return;
    }

    pthread_mutex_lock(&client->lock);
    if (client->connected && client->socket_fd >= 0) {
        close(client->socket_fd);
    }
    client->connected = false;
    client->socket_fd = -1;
    pthread_mutex_unlock(&client->lock);
    pthread_mutex_destroy(&client->lock);
}

int legomem_qemu_read(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, void *data, unsigned size) {
    if (!client || !data) {
        return -1;
    }

    size_t done = 0;
    while (done < size) {
        size_t chunk = size - done > LEGOMEM_QEMU_MAX_TRANSFER_SIZE ?
            LEGOMEM_QEMU_MAX_TRANSFER_SIZE : size - done;

        if (send_request(client, LEGOMEM_QEMU_OP_READ, region_id,
                         offset + done, NULL, (uint8_t *)data + done,
                         chunk) < 0) {
            return -1;
        }

        client->total_reads++;
        done += chunk;
    }

    return 0;
}

int legomem_qemu_write(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, const void *data, unsigned size) {
    if (!client || !data) {
        return -1;
    }

    size_t done = 0;
    while (done < size) {
        size_t chunk = size - done > LEGOMEM_QEMU_MAX_TRANSFER_SIZE ?
            LEGOMEM_QEMU_MAX_TRANSFER_SIZE : size - done;

        if (send_request(client, LEGOMEM_QEMU_OP_WRITE, region_id,
                         offset + done, (const uint8_t *)data + done, NULL,
                         chunk) < 0) {
            return -1;
        }

        client->total_writes++;
        done += chunk;
    }

    return 0;
}

int legomem_qemu_fence(LegoMemQemuClient *client, uint64_t region_id) {
    if (!client) {
        return -1;
    }

    return send_request(client, LEGOMEM_QEMU_OP_FENCE, region_id, 0,
                        NULL, NULL, 0);
}

int legomem_qemu_flush(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, unsigned size) {
    if (!client) {
        return -1;
    }

    return send_request(client, LEGOMEM_QEMU_OP_FLUSH, region_id, offset,
                        NULL, NULL, size);
}
