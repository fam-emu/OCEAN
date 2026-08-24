/*
 * stress_cxl_server.c - Host-side reproduction of the cxlmemsim_server
 * connection-burst SIGSEGV.
 *
 * Mimics what QEMU's cxl-type3 backend does at CXL device init: opens many
 * TCP connections near-simultaneously and floods the server with READ/WRITE
 * requests to a spread of addresses. This grows the CXL expander's
 * `occupation` vector while concurrent connection threads sort / rebuild its
 * address caches -- the exact data race that corrupts the vector's heap and
 * crashes the server (historically surfacing as a bogus std::queue<int>
 * push/pop fault).
 *
 * Build:  gcc -O2 -pthread -o stress_cxl_server stress_cxl_server.c
 * Run:    ./stress_cxl_server [host] [port] [num_conns] [ops_per_conn]
 *
 * Exit status:
 *   0  = all requests succeeded, server still alive
 *   1  = a request failed / connection dropped (server likely crashed)
 *   2  = setup error (couldn't connect / handshake)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <pthread.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>

#define DATA_SIZE 2048

struct __attribute__((packed)) Req {
    uint8_t  op;         /* 0=READ 1=WRITE 2=GET_SHM_INFO */
    uint64_t addr, size, ts, value, expected;
    uint8_t  data[DATA_SIZE];
};
struct __attribute__((packed)) Resp {
    uint8_t  status;
    uint64_t latency, old;
    uint8_t  data[DATA_SIZE];
};
struct __attribute__((packed)) ShmInfo {
    uint8_t  status;
    uint64_t base, size, ncl;
    char     name[256];
};

static const char *g_host = "127.0.0.1";
static int   g_port = 9999;
static long  g_ops  = 20000;
static volatile int g_fail = 0;

/* start barrier so every connection fires its burst at once */
static pthread_barrier_t g_barrier;

static int send_all(int fd, const void *buf, size_t n) {
    const char *p = buf; size_t off = 0;
    while (off < n) {
        ssize_t s = send(fd, p + off, n - off, 0);
        if (s <= 0) return -1;
        off += (size_t)s;
    }
    return 0;
}
static int recv_all(int fd, void *buf, size_t n) {
    ssize_t r = recv(fd, buf, n, MSG_WAITALL);
    return (r == (ssize_t)n) ? 0 : -1;
}

static void *worker(void *arg) {
    long id = (long)arg;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { g_fail = 1; return NULL; }
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    struct sockaddr_in sa = {0};
    sa.sin_family = AF_INET;
    sa.sin_port   = htons(g_port);
    inet_pton(AF_INET, g_host, &sa.sin_addr);
    if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) != 0) {
        fprintf(stderr, "conn %ld: connect failed\n", id);
        g_fail = 2; close(fd); return NULL;
    }

    /* Handshake: GET_SHM_INFO -> learn address range */
    struct Req  q; memset(&q, 0, sizeof(q));
    struct ShmInfo si; memset(&si, 0, sizeof(si));
    q.op = 2;
    if (send_all(fd, &q, sizeof(q)) || recv_all(fd, &si, sizeof(si))) {
        fprintf(stderr, "conn %ld: handshake failed\n", id);
        g_fail = 2; close(fd); return NULL;
    }
    uint64_t ncl = si.ncl ? si.ncl : (2048UL * 1024 * 1024 / 64);
    uint64_t base = si.base;

    pthread_barrier_wait(&g_barrier);   /* fire the burst together */

    struct Resp r;
    unsigned seed = (unsigned)(id * 2654435761u);
    for (long i = 0; i < g_ops && !g_fail; i++) {
        memset(&q, 0, sizeof(q));
        /* spread across the whole range to keep `occupation` growing/sorting */
        uint64_t cl = (uint64_t)(rand_r(&seed)) % ncl;
        q.op   = (i & 1) ? 1 : 0;             /* alternate WRITE/READ */
        q.addr = base + cl * 64;
        q.size = 64;
        q.ts   = (uint64_t)i * 100 + (uint64_t)id;
        if (q.op == 1) memset(q.data, (int)(id & 0xff), 64);

        if (send_all(fd, &q, sizeof(q)) || recv_all(fd, &r, sizeof(r))) {
            fprintf(stderr, "conn %ld: I/O failed at op %ld (server crash?)\n", id, i);
            g_fail = 1; break;
        }
        if (r.status != 0) {
            /* status!=0 for an in-range addr is unexpected */
            fprintf(stderr, "conn %ld: bad status %u at op %ld addr=0x%lx\n",
                    id, r.status, i, (unsigned long)q.addr);
            g_fail = 1; break;
        }
    }
    close(fd);
    return NULL;
}

int main(int argc, char **argv) {
    long nconn = 16;
    if (argc > 1) g_host = argv[1];
    if (argc > 2) g_port = atoi(argv[2]);
    if (argc > 3) nconn  = atol(argv[3]);
    if (argc > 4) g_ops  = atol(argv[4]);

    printf("stress: host=%s port=%d conns=%ld ops/conn=%ld\n",
           g_host, g_port, nconn, g_ops);

    pthread_barrier_init(&g_barrier, NULL, (unsigned)nconn);
    pthread_t *th = calloc(nconn, sizeof(pthread_t));
    for (long i = 0; i < nconn; i++)
        pthread_create(&th[i], NULL, worker, (void *)i);
    for (long i = 0; i < nconn; i++)
        pthread_join(th[i], NULL);
    pthread_barrier_destroy(&g_barrier);
    free(th);

    if (g_fail == 2) { printf("RESULT: SETUP-ERROR\n"); return 2; }
    if (g_fail)      { printf("RESULT: FAIL (server dropped/crashed)\n"); return 1; }
    printf("RESULT: OK (%ld conns x %ld ops all succeeded)\n", nconn, g_ops);
    return 0;
}
