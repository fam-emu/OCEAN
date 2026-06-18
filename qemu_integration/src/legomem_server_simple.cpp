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

        std::cout << "LegoMem server listening on port " << port << "\n";
        return true;
    }

    void handle_client(int client_fd) {
        while (running) {
            LegoMemQemuRequest req{};
            ssize_t received = recv(client_fd, &req, sizeof(req), MSG_WAITALL);
            if (received == 0) {
                break;
            }
            if (received != static_cast<ssize_t>(sizeof(req))) {
                std::cerr << "failed to receive LegoMem request\n";
                break;
            }

            LegoMemQemuResponse resp{};
            resp.status = LEGOMEM_QEMU_STATUS_OK;

            if (req.size > LEGOMEM_QEMU_CACHELINE_SIZE) {
                resp.status = LEGOMEM_QEMU_STATUS_ERR;
            } else if (req.op_type == LEGOMEM_QEMU_OP_READ) {
                handle_read(req, resp);
            } else if (req.op_type == LEGOMEM_QEMU_OP_WRITE) {
                handle_write(req, resp);
            } else if (req.op_type == LEGOMEM_QEMU_OP_FENCE || req.op_type == LEGOMEM_QEMU_OP_FLUSH) {
                resp.latency_ns = 0;
            } else {
                resp.status = LEGOMEM_QEMU_STATUS_ERR;
            }

            ssize_t sent = send(client_fd, &resp, sizeof(resp), 0);
            if (sent != static_cast<ssize_t>(sizeof(resp))) {
                std::cerr << "failed to send LegoMem response\n";
                break;
            }
        }

        close(client_fd);
    }

    void handle_read(const LegoMemQemuRequest &req, LegoMemQemuResponse &resp) {
        std::lock_guard<std::mutex> lock(storage_mutex);
        uint64_t line_offset = cacheline_offset(req.offset);
        uint64_t in_line = req.offset - line_offset;
        RegionLine &line = storage[{req.region_id, line_offset}];
        size_t copy_size = std::min<uint64_t>(req.size, LEGOMEM_QEMU_CACHELINE_SIZE - in_line);

        memcpy(resp.data, line.data.data() + in_line, copy_size);
        line.access_count++;
        resp.latency_ns = 0;
    }

    void handle_write(const LegoMemQemuRequest &req, LegoMemQemuResponse &resp) {
        std::lock_guard<std::mutex> lock(storage_mutex);
        uint64_t line_offset = cacheline_offset(req.offset);
        uint64_t in_line = req.offset - line_offset;
        RegionLine &line = storage[{req.region_id, line_offset}];
        size_t copy_size = std::min<uint64_t>(req.size, LEGOMEM_QEMU_CACHELINE_SIZE - in_line);

        memcpy(line.data.data() + in_line, req.data, copy_size);
        line.access_count++;
        line.version++;
        resp.latency_ns = 0;
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
