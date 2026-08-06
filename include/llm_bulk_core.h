/*
 * Dependency-light CXLMemSim bulk request path.
 *
 * The legacy CXLController/CXLMemExpander entry points are coupled to Linux
 * PMU, CPU ROB, and server code.  These explicit byte/ns types expose the
 * controller -> HDM decoder -> expander service path needed by offline LLM
 * replay without pulling those transports into the standalone binary.
 */

#ifndef CXLMEMSIM_LLM_BULK_CORE_H
#define CXLMEMSIM_LLM_BULK_CORE_H

#include "hdm_decoder.h"

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace cxlmemsim::bulk {

struct BulkCoreProfile {
    std::string path_mode = "direct_dma";
    double base_latency_ns = 0.0;
    double media_latency_ns = 0.0;
    double topology_latency_ns = 0.0;
    double read_bandwidth_gib_s = 0.0;
    double write_bandwidth_gib_s = 0.0;
    double gpu_link_bandwidth_gib_s = 0.0;
    double local_dram_bandwidth_gib_s = 0.0;
    double local_to_gpu_latency_ns = 0.0;
    uint64_t capacity_bytes = 0;
    uint32_t num_expanders = 1;
    uint32_t num_ports = 1;
    uint32_t max_outstanding_requests = 1;
    std::string congestion_model = "fifo";
};

struct BulkMemoryRequest {
    std::string event_id;
    uint64_t issue_time_ns = 0;
    uint64_t need_time_ns = 0;
    uint64_t logical_address = 0;
    uint64_t size_bytes = 0;
    bool is_write = false;
    uint32_t client_id = 0;
    uint32_t queue_id = 0;
    uint64_t transfer_granularity_bytes = 0;
    uint64_t chunk_bytes = 0;
    double dependency_ready_ns = 0.0;
};

struct BulkMemoryCompletion {
    std::string event_id;
    double service_start_ns = 0.0;
    double service_end_ns = 0.0;
    double queue_delay_ns = 0.0;
    double topology_delay_ns = 0.0;
    double media_delay_ns = 0.0;
    double bandwidth_delay_ns = 0.0;
    double congestion_delay_ns = 0.0;
    double base_delay_ns = 0.0;
    double effective_bandwidth_gib_s = 0.0;
    uint64_t requested_bytes = 0;
    uint64_t modeled_bytes = 0;
    uint32_t endpoint_id = 0;
    uint32_t port_id = 0;
    uint64_t chunk_count = 0;
    bool capacity_hit = true;
};

struct BulkCoreDebugCounters {
    uint64_t controller_service_calls = 0;
    uint64_t hdm_decode_calls = 0;
    uint64_t expander_service_calls = 0;
};

class CXLMemSimBulkExpander {
  public:
    struct ChunkCompletion {
        double service_start_ns = 0.0;
        double service_end_ns = 0.0;
        double base_delay_ns = 0.0;
        double media_delay_ns = 0.0;
        double topology_delay_ns = 0.0;
        double bandwidth_delay_ns = 0.0;
        double congestion_delay_ns = 0.0;
        double effective_bandwidth_gib_s = 0.0;
        uint32_t port_id = 0;
    };

    CXLMemSimBulkExpander(uint32_t endpoint_id, const BulkCoreProfile &profile);

    ChunkCompletion serviceChunk(double ready_time_ns, uint64_t size_bytes, bool is_write);
    uint64_t serviceCallCount() const noexcept { return service_calls_; }

  private:
    BulkCoreProfile profile_;
    std::vector<double> port_ready_ns_;
    uint64_t service_calls_ = 0;
};

class CXLMemSimBulkController {
  public:
    explicit CXLMemSimBulkController(BulkCoreProfile profile);

    BulkMemoryCompletion service(const BulkMemoryRequest &request);
    BulkCoreDebugCounters debugCounters() const noexcept;
    std::string effectiveTopology() const;

  private:
    BulkCoreProfile profile_;
    HDMDecoder decoder_;
    std::vector<std::unique_ptr<CXLMemSimBulkExpander>> expanders_;
    uint64_t controller_service_calls_ = 0;
    uint64_t hdm_decode_calls_ = 0;
};

} // namespace cxlmemsim::bulk

#endif // CXLMEMSIM_LLM_BULK_CORE_H
