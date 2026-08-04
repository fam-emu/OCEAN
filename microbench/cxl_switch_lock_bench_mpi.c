#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <mpi.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <x86intrin.h>

#define CACHE_LINE 64u
#define STREAM_LINES 512u
#define MIN_MAP_SIZE (2u * 1024u * 1024u)

struct options {
    const char *dax_path;
    uint64_t iterations;
    off_t map_offset;
    size_t map_size;
    bool acknowledge_write;
    bool self_test;
};

static void usage(const char *program) {
    fprintf(stderr,
            "Usage: %s [--self-test] [--dax PATH --acknowledge-dax-write] "
            "[--iterations N] [--map-offset BYTES] [--map-size BYTES]\n",
            program);
}

static bool parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    errno = 0;
    unsigned long long parsed = strtoull(text, &end, 0);
    if (errno != 0 || end == text || *end != '\0') {
        return false;
    }
    *value = (uint64_t)parsed;
    return true;
}

static bool parse_options(int argc, char **argv, struct options *options) {
    *options = (struct options){
        .dax_path = NULL,
        .iterations = 10000,
        .map_offset = 0,
        .map_size = MIN_MAP_SIZE,
        .acknowledge_write = false,
        .self_test = false,
    };
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--self-test") == 0) {
            options->self_test = true;
        } else if (strcmp(argv[i], "--acknowledge-dax-write") == 0) {
            options->acknowledge_write = true;
        } else if (strcmp(argv[i], "--dax") == 0 && i + 1 < argc) {
            options->dax_path = argv[++i];
        } else if (strcmp(argv[i], "--iterations") == 0 && i + 1 < argc) {
            if (!parse_u64(argv[++i], &options->iterations) || options->iterations == 0) {
                return false;
            }
        } else if (strcmp(argv[i], "--map-offset") == 0 && i + 1 < argc) {
            uint64_t value = 0;
            if (!parse_u64(argv[++i], &value)) {
                return false;
            }
            options->map_offset = (off_t)value;
        } else if (strcmp(argv[i], "--map-size") == 0 && i + 1 < argc) {
            uint64_t value = 0;
            if (!parse_u64(argv[++i], &value) || value < MIN_MAP_SIZE) {
                return false;
            }
            options->map_size = (size_t)value;
        } else {
            return false;
        }
    }
    return true;
}

static inline uint64_t ns_now(void) {
    struct timespec timestamp;
    clock_gettime(CLOCK_MONOTONIC_RAW, &timestamp);
    return (uint64_t)timestamp.tv_sec * 1000000000ull + (uint64_t)timestamp.tv_nsec;
}

static inline double elapsed_ns(uint64_t start, uint64_t end) {
    return end > start ? (double)(end - start) : 1.0;
}

static inline void flush_sender(void *address) {
    _mm_clflushopt(address);
    _mm_sfence();
}

static inline uint64_t invalidate_load(volatile uint64_t *address) {
    _mm_clflush((const void *)address);
    _mm_mfence();
    return *address;
}

static inline bool compare_exchange(volatile uint64_t *address, uint64_t expected,
                                    uint64_t desired) {
    return __atomic_compare_exchange_n(address, &expected, desired, false,
                                       __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}

static void emit_metadata(int rank, int world_size, const struct options *options) {
    printf("{\"type\":\"metadata\",\"version\":1,\"rank\":%d,"
           "\"world_size\":%d,\"iterations\":%" PRIu64 ","
           "\"dax_path\":\"%s\",\"map_offset\":%lld,\"map_size\":%zu}\n",
           rank, world_size, options->iterations,
           options->self_test ? "anonymous-self-test" : options->dax_path,
           (long long)options->map_offset, options->map_size);
    fflush(stdout);
}

static void emit_sample(const char *operation, uint64_t sample_id, double latency_ns) {
    printf("{\"type\":\"sample\",\"operation\":\"%s\","
           "\"sample_id\":%" PRIu64 ",\"latency_ns\":%.3f}\n",
           operation, sample_id, fmax(latency_ns, 1.0));
}

static void emit_summary(const char *name, double value) {
    printf("{\"type\":\"summary\",\"name\":\"%s\",\"value\":%.3f}\n",
           name, fmax(value, 0.001));
}

static void emit_contention(unsigned lock_count, double utilization,
                            double added_latency_ns) {
    printf("{\"type\":\"contention\",\"lock_count\":%u,"
           "\"effective_utilization\":%.6f,\"added_latency_ns\":%.3f}\n",
           lock_count, utilization, fmax(added_latency_ns, 0.0));
}

static double measure_flush(volatile uint64_t *address) {
    *address += 1;
    uint64_t start = ns_now();
    flush_sender((void *)address);
    return elapsed_ns(start, ns_now());
}

static double measure_invalidate_load(volatile uint64_t *address) {
    uint64_t start = ns_now();
    volatile uint64_t value = invalidate_load(address);
    (void)value;
    return elapsed_ns(start, ns_now());
}

static double measure_cas(volatile uint64_t *address, bool flush_first) {
    *address = 0;
    if (flush_first) {
        flush_sender((void *)address);
    }
    uint64_t start = ns_now();
    bool exchanged = compare_exchange(address, 0, 1);
    double duration = elapsed_ns(start, ns_now());
    if (!exchanged) {
        return NAN;
    }
    return duration;
}

static double measure_full_round_trip(volatile uint64_t *address) {
    uint64_t start = ns_now();
    *address += 1;
    flush_sender((void *)address);
    volatile uint64_t value = invalidate_load(address);
    (void)value;
    return elapsed_ns(start, ns_now());
}

static double ping_pong_round_trip(volatile uint8_t *mapping,
                                   const struct options *options, int rank) {
    volatile uint64_t *request =
        (volatile uint64_t *)(mapping + CACHE_LINE * 4u);
    volatile uint64_t *response =
        (volatile uint64_t *)(mapping + CACHE_LINE * 5u);
    if (rank == 0) {
        *request = 0;
        *response = 0;
        flush_sender((void *)request);
        flush_sender((void *)response);
    }
    MPI_Barrier(MPI_COMM_WORLD);

    double total = 0.0;
    for (uint64_t sample = 0; sample < options->iterations; ++sample) {
        uint64_t token = sample + 1;
        if (rank == 0) {
            uint64_t start = ns_now();
            *request = token;
            flush_sender((void *)request);
            while (invalidate_load(response) != token) {
                _mm_pause();
            }
            double duration = elapsed_ns(start, ns_now());
            emit_sample("full_rt", sample, duration);
            total += duration;
        } else {
            while (invalidate_load(request) != token) {
                _mm_pause();
            }
            *response = token;
            flush_sender((void *)response);
        }
    }
    MPI_Barrier(MPI_COMM_WORLD);
    return rank == 0 ? total / (double)options->iterations : 0.0;
}

static double measure_stream_gap(volatile uint8_t *base) {
    uint64_t start = ns_now();
    for (unsigned line = 0; line < STREAM_LINES; ++line) {
        flush_sender((void *)(base + line * CACHE_LINE));
    }
    return elapsed_ns(start, ns_now()) / STREAM_LINES;
}

static double contention_phase(volatile uint64_t *locks, unsigned lock_count,
                               uint64_t iterations) {
    MPI_Barrier(MPI_COMM_WORLD);
    uint64_t start = ns_now();
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        volatile uint64_t *lock = &locks[iteration % lock_count];
        while (!compare_exchange(lock, 0, 1)) {
            _mm_pause();
        }
        __atomic_store_n(lock, 0, __ATOMIC_RELEASE);
    }
    double average = elapsed_ns(start, ns_now()) / (double)iterations;
    MPI_Barrier(MPI_COMM_WORLD);
    return average;
}

static int run_benchmark(volatile uint8_t *mapping, const struct options *options,
                         int rank, int world_size) {
    volatile uint64_t *value = (volatile uint64_t *)mapping;
    volatile uint64_t *cas_raw =
        (volatile uint64_t *)(mapping + CACHE_LINE);
    volatile uint64_t *cas_flush =
        (volatile uint64_t *)(mapping + CACHE_LINE * 2u);
    volatile uint64_t *locks = (volatile uint64_t *)(mapping + CACHE_LINE * 16u);
    emit_metadata(rank, world_size, options);
    MPI_Barrier(MPI_COMM_WORLD);

    double os_total = 0.0;
    double or_total = 0.0;
    double full_total = 0.0;
    double cas_raw_total = 0.0;
    if (rank == 0) {
        for (uint64_t sample = 0; sample < options->iterations; ++sample) {
            double os_ns = measure_flush(value);
            double cas_raw_ns = measure_cas(cas_raw, false);
            double cas_flush_ns = measure_cas(cas_flush, true);
            double or_ns = measure_invalidate_load(value);
            double full_ns = options->self_test ? measure_full_round_trip(value) : 0.0;
            if (!isfinite(cas_raw_ns) || !isfinite(cas_flush_ns)) {
                fprintf(stderr, "CAS measurement failed\n");
                return 1;
            }
            emit_sample("os", sample, os_ns);
            emit_sample("cas_raw", sample, cas_raw_ns);
            emit_sample("cas_flush", sample, cas_flush_ns);
            emit_sample("or", sample, or_ns);
            if (options->self_test) {
                emit_sample("full_rt", sample, full_ns);
            }
            os_total += os_ns;
            or_total += or_ns;
            cas_raw_total += cas_raw_ns;
            if (options->self_test) {
                full_total += full_ns;
            }
        }
    }

    double rtt_ns = options->self_test
                        ? full_total / (double)options->iterations
                        : ping_pong_round_trip(mapping, options, rank);
    if (rank == 0) {
        emit_summary("os_ns", os_total / (double)options->iterations);
        emit_summary("or_ns", or_total / (double)options->iterations);
        emit_summary("rtt_ns", rtt_ns);
        emit_summary("g_ns", measure_stream_gap(mapping + CACHE_LINE * 64u));
        fflush(stdout);
    }

    double baseline =
        rank == 0 ? cas_raw_total / (double)options->iterations : 0.0;
    const unsigned lock_counts[] = {1, 2, 4, 8};
    for (size_t index = 0; index < sizeof(lock_counts) / sizeof(lock_counts[0]); ++index) {
        unsigned lock_count = lock_counts[index];
        for (unsigned lock = 0; lock < lock_count; ++lock) {
            locks[lock] = 0;
        }
        MPI_Barrier(MPI_COMM_WORLD);
        double average = contention_phase(locks, lock_count, options->iterations);
        double max_average = 0.0;
        MPI_Reduce(&average, &max_average, 1, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);
        if (rank == 0) {
            emit_contention(lock_count, 1.0 / (double)lock_count,
                            fmax(max_average - baseline, 0.0));
            fflush(stdout);
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank = 0;
    int world_size = 0;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &world_size);

    struct options options;
    if (!parse_options(argc, argv, &options)) {
        if (rank == 0) {
            usage(argv[0]);
        }
        MPI_Finalize();
        return 2;
    }
    if (!__builtin_cpu_supports("clflushopt")) {
        if (rank == 0) {
            fprintf(stderr, "CPU does not support clflushopt\n");
        }
        MPI_Finalize();
        return 2;
    }
    if (!options.self_test && world_size != 2) {
        if (rank == 0) {
            fprintf(stderr, "hardware benchmark requires exactly two MPI ranks\n");
        }
        MPI_Finalize();
        return 2;
    }
    if (options.self_test && world_size != 1) {
        if (rank == 0) {
            fprintf(stderr, "self-test requires exactly one MPI rank\n");
        }
        MPI_Finalize();
        return 2;
    }

    void *mapping = MAP_FAILED;
    int dax_fd = -1;
    if (options.self_test) {
        mapping = mmap(NULL, options.map_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    } else {
        long page_size = sysconf(_SC_PAGESIZE);
        if (!options.acknowledge_write || options.dax_path == NULL) {
            if (rank == 0) {
                fprintf(stderr, "DAX access requires --dax and --acknowledge-dax-write\n");
            }
            MPI_Finalize();
            return 2;
        }
        if (page_size <= 0 || options.map_offset % page_size != 0) {
            if (rank == 0) {
                fprintf(stderr, "map offset must be page aligned\n");
            }
            MPI_Finalize();
            return 2;
        }
        if (rank == 0) {
            fprintf(stderr, "writing DAX byte range [%lld, %lld) on %s\n",
                    (long long)options.map_offset,
                    (long long)options.map_offset + (long long)options.map_size,
                    options.dax_path);
        }
        dax_fd = open(options.dax_path, O_RDWR | O_SYNC);
        if (dax_fd >= 0) {
            mapping = mmap(NULL, options.map_size, PROT_READ | PROT_WRITE, MAP_SHARED,
                           dax_fd, options.map_offset);
        }
    }

    int local_failed = mapping == MAP_FAILED;
    int any_failed = 0;
    MPI_Allreduce(&local_failed, &any_failed, 1, MPI_INT, MPI_MAX, MPI_COMM_WORLD);
    if (any_failed) {
        if (rank == 0) {
            fprintf(stderr, "memory mapping failed: %s\n", strerror(errno));
        }
        if (mapping != MAP_FAILED) {
            munmap(mapping, options.map_size);
        }
        if (dax_fd >= 0) {
            close(dax_fd);
        }
        MPI_Finalize();
        return 2;
    }

    if (rank == 0) {
        memset(mapping, 0, options.map_size);
    }
    MPI_Barrier(MPI_COMM_WORLD);
    int status = run_benchmark((volatile uint8_t *)mapping, &options, rank, world_size);
    munmap(mapping, options.map_size);
    if (dax_fd >= 0) {
        close(dax_fd);
    }
    MPI_Finalize();
    return status;
}
