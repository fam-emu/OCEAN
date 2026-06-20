#include "qemu_legomem.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <mutex>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>

class LegoMemServer {
private:
    struct RegionLine {
        std::array<uint8_t, LEGOMEM_QEMU_CACHELINE_SIZE> data{};
        uint64_t access_count = 0;
        uint64_t version = 0;
    };

    int server_fd = -1;
    int port;
    std::atomic<bool> running{true};
    std::map<std::pair<uint64_t, uint64_t>, RegionLine> storage;
    std::mutex storage_mutex;

    static uint64_t cacheline_offset(uint64_t offset) {
        return offset & ~(static_cast<uint64_t>(LEGOMEM_QEMU_CACHELINE_SIZE) - 1);
    }

    static bool transfer_too_large(const LegoMemQemuRequestHeader &req) {
        return (req.op_type == LEGOMEM_QEMU_OP_READ ||
                req.op_type == LEGOMEM_QEMU_OP_WRITE) &&
               req.size > LEGOMEM_QEMU_MAX_TRANSFER_SIZE;
    }

    static bool recv_all(int fd, void *buf, size_t len) {
        auto *pos = static_cast<uint8_t *>(buf);

        while (len > 0) {
            ssize_t received = recv(fd, pos, len, MSG_WAITALL);
            if (received <= 0) {
                return false;
            }

            pos += received;
            len -= static_cast<size_t>(received);
        }

        return true;
    }

    static bool send_all(int fd, const void *buf, size_t len) {
        const auto *pos = static_cast<const uint8_t *>(buf);

        while (len > 0) {
            ssize_t sent = send(fd, pos, len, 0);
            if (sent <= 0) {
                return false;
            }

            pos += sent;
            len -= static_cast<size_t>(sent);
        }

        return true;
    }

public:
    explicit LegoMemServer(int port) : port(port) {}

    bool start() {
        server_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (server_fd < 0) {
            std::cerr << "failed to create socket\n";
            return false;
        }

        int opt = 1;
        if (setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)) < 0) {
            std::cerr << "failed to set socket options\n";
            return false;
        }

        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = INADDR_ANY;
        address.sin_port = htons(port);

        if (bind(server_fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
            std::cerr << "failed to bind to port " << port << "\n";
            return false;
        }

        if (listen(server_fd, 16) < 0) {
            std::cerr << "failed to listen on socket\n";
            return false;
        }

        std::cout << "LegoMem server listening on port " << port << std::endl;
        return true;
    }

    void handle_client(int client_fd) {
        while (running) {
            LegoMemQemuRequestHeader req{};
            std::vector<uint8_t> request_data;
            std::vector<uint8_t> response_data;
            LegoMemQemuResponseHeader resp{};
            resp.status = LEGOMEM_QEMU_STATUS_OK;

            if (!recv_all(client_fd, &req, sizeof(req))) {
                break;
            }

            if (transfer_too_large(req)) {
                resp.status = LEGOMEM_QEMU_STATUS_ERR;
            } else if (req.op_type == LEGOMEM_QEMU_OP_READ) {
                handle_read(req, response_data);
                resp.size = response_data.size();
            } else if (req.op_type == LEGOMEM_QEMU_OP_WRITE) {
                request_data.resize(static_cast<size_t>(req.size));
                if (!recv_all(client_fd, request_data.data(), request_data.size())) {
                    break;
                }
                handle_write(req, request_data);
            } else if (req.op_type == LEGOMEM_QEMU_OP_FENCE || req.op_type == LEGOMEM_QEMU_OP_FLUSH) {
                resp.latency_ns = 0;
            } else {
                resp.status = LEGOMEM_QEMU_STATUS_ERR;
            }

            if (!send_all(client_fd, &resp, sizeof(resp))) {
                std::cerr << "failed to send LegoMem response\n";
                break;
            }
            if (resp.status == LEGOMEM_QEMU_STATUS_OK &&
                req.op_type == LEGOMEM_QEMU_OP_READ && !response_data.empty() &&
                !send_all(client_fd, response_data.data(), response_data.size())) {
                std::cerr << "failed to send LegoMem response payload\n";
                break;
            }
        }

        close(client_fd);
    }

    void handle_read(const LegoMemQemuRequestHeader &req,
                     std::vector<uint8_t> &response_data) {
        std::lock_guard<std::mutex> lock(storage_mutex);
        size_t done = 0;

        response_data.resize(static_cast<size_t>(req.size));
        while (done < response_data.size()) {
            uint64_t offset = req.offset + done;
            uint64_t line_offset = cacheline_offset(offset);
            uint64_t in_line = offset - line_offset;
            size_t copy_size = std::min<uint64_t>(
                response_data.size() - done,
                LEGOMEM_QEMU_CACHELINE_SIZE - in_line);
            RegionLine &line = storage[{req.region_id, line_offset}];

            memcpy(response_data.data() + done, line.data.data() + in_line,
                   copy_size);
            line.access_count++;
            done += copy_size;
        }
    }

    void handle_write(const LegoMemQemuRequestHeader &req,
                      const std::vector<uint8_t> &request_data) {
        std::lock_guard<std::mutex> lock(storage_mutex);
        size_t done = 0;

        while (done < request_data.size()) {
            uint64_t offset = req.offset + done;
            uint64_t line_offset = cacheline_offset(offset);
            uint64_t in_line = offset - line_offset;
            size_t copy_size = std::min<uint64_t>(
                request_data.size() - done,
                LEGOMEM_QEMU_CACHELINE_SIZE - in_line);
            RegionLine &line = storage[{req.region_id, line_offset}];

            memcpy(line.data.data() + in_line, request_data.data() + done,
                   copy_size);
            line.access_count++;
            line.version++;
            done += copy_size;
        }
    }

    void run() {
        while (running) {
            sockaddr_in client_addr{};
            socklen_t client_len = sizeof(client_addr);
            int client_fd = accept(server_fd, reinterpret_cast<sockaddr *>(&client_addr), &client_len);
            if (client_fd < 0) {
                if (running) {
                    std::cerr << "failed to accept connection\n";
                }
                continue;
            }

            int nodelay = 1;
            setsockopt(client_fd, IPPROTO_TCP, TCP_NODELAY, &nodelay,
                       sizeof(nodelay));

            std::thread(&LegoMemServer::handle_client, this, client_fd).detach();
        }
    }

    void stop() {
        running = false;
        if (server_fd >= 0) {
            close(server_fd);
        }
    }
};

static LegoMemServer *g_server = nullptr;

int main(int argc, char *argv[]) {
    int port = argc > 1 ? std::atoi(argv[1]) : 9999;
    LegoMemServer server(port);
    g_server = &server;

    if (!server.start()) {
        return 1;
    }

    std::signal(SIGINT, [](int) {
        if (g_server) {
            g_server->stop();
        }
        std::exit(0);
    });

    server.run();
    return 0;
}
