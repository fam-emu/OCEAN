#include "qemu_legomem.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
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

static int connect_to_server(LegoMemQemuClient *client) {
    struct sockaddr_in server_addr;

    client->socket_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (client->socket_fd < 0) {
        perror("legomem_qemu: socket");
        return -1;
    }

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

static int send_request(LegoMemQemuClient *client, LegoMemQemuRequest *req, LegoMemQemuResponse *resp) {
    pthread_mutex_lock(&client->lock);

    if (!client->connected && connect_to_server(client) < 0) {
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    ssize_t sent = send(client->socket_fd, req, sizeof(*req), 0);
    if (sent != (ssize_t)sizeof(*req)) {
        perror("legomem_qemu: send");
        client->connected = false;
        close(client->socket_fd);
        client->socket_fd = -1;
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    ssize_t received = recv(client->socket_fd, resp, sizeof(*resp), MSG_WAITALL);
    if (received != (ssize_t)sizeof(*resp)) {
        perror("legomem_qemu: recv");
        client->connected = false;
        close(client->socket_fd);
        client->socket_fd = -1;
        pthread_mutex_unlock(&client->lock);
        return -1;
    }

    pthread_mutex_unlock(&client->lock);
    return resp->status == LEGOMEM_QEMU_STATUS_OK ? 0 : -1;
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
        LegoMemQemuRequest req = {0};
        LegoMemQemuResponse resp = {0};
        size_t chunk = size - done > LEGOMEM_QEMU_CACHELINE_SIZE ? LEGOMEM_QEMU_CACHELINE_SIZE : size - done;

        req.op_type = LEGOMEM_QEMU_OP_READ;
        req.region_id = region_id ? region_id : client->default_region_id;
        req.offset = offset + done;
        req.size = chunk;
        req.timestamp = get_timestamp_ns();

        if (send_request(client, &req, &resp) < 0) {
            return -1;
        }

        memcpy((uint8_t *)data + done, resp.data, chunk);
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
        LegoMemQemuRequest req = {0};
        LegoMemQemuResponse resp = {0};
        size_t chunk = size - done > LEGOMEM_QEMU_CACHELINE_SIZE ? LEGOMEM_QEMU_CACHELINE_SIZE : size - done;

        req.op_type = LEGOMEM_QEMU_OP_WRITE;
        req.region_id = region_id ? region_id : client->default_region_id;
        req.offset = offset + done;
        req.size = chunk;
        req.timestamp = get_timestamp_ns();
        memcpy(req.data, (const uint8_t *)data + done, chunk);

        if (send_request(client, &req, &resp) < 0) {
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

    LegoMemQemuRequest req = {0};
    LegoMemQemuResponse resp = {0};
    req.op_type = LEGOMEM_QEMU_OP_FENCE;
    req.region_id = region_id ? region_id : client->default_region_id;
    req.timestamp = get_timestamp_ns();
    return send_request(client, &req, &resp);
}

int legomem_qemu_flush(LegoMemQemuClient *client, uint64_t region_id, uint64_t offset, unsigned size) {
    if (!client) {
        return -1;
    }

    LegoMemQemuRequest req = {0};
    LegoMemQemuResponse resp = {0};
    req.op_type = LEGOMEM_QEMU_OP_FLUSH;
    req.region_id = region_id ? region_id : client->default_region_id;
    req.offset = offset;
    req.size = size;
    req.timestamp = get_timestamp_ns();
    return send_request(client, &req, &resp);
}
