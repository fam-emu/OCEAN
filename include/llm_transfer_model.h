/*
 * OCEAN/CXLMemSim bulk-transfer model for LLM tensor and KV-block traces.
 *
 * This API is intentionally independent of the CPU ROB and QEMU transports.
 * Time is expressed in nanoseconds and bandwidth in GiB/s throughout.
 */

#ifndef CXLMEMSIM_LLM_TRANSFER_MODEL_H
#define CXLMEMSIM_LLM_TRANSFER_MODEL_H

#include <cstdint>
#include <string>
#include <vector>

namespace cxlmemsim::llm {

enum class ReplayMode { Auto, Aggregate, Detailed };

struct HardwareProfile {
    int schema_version = 1;
    std::string profile_name = "unnamed";
    std::string path_mode = "direct_dma";
    double base_latency_ns = 150.0;
    double media_latency_ns = 100.0;
    double read_bandwidth_gib_s = 32.0;
    double write_bandwidth_gib_s = 24.0;
    uint64_t capacity_bytes = 64ULL * 1024ULL * 1024ULL * 1024ULL;
    uint32_t num_expanders = 1;
    uint32_t switch_hops = 0;
    double per_hop_latency_ns = 50.0;
    uint32_t num_ports = 1;
    uint32_t num_clients = 1;
    uint32_t queue_depth = 64;
    uint32_t max_outstanding_requests = 64;
    std::string congestion_model = "fifo";
    uint64_t transfer_granularity_bytes = 4096;
    double gpu_link_bandwidth_gib_s = 52.0;
    double local_dram_bandwidth_gib_s = 40.0;
    double local_to_gpu_latency_ns = 3000.0;
    uint64_t detailed_threshold_bytes = 1024ULL * 1024ULL;
};

struct TransferRequest {
    int schema_version = 1;
    std::string event_id;
    std::string request_id;
    std::string object_id;
    std::string object_type;
    std::string data_type;
    std::string phase;
    int64_t layer_id = -1;
    uint64_t issue_time_ns = 0;
    uint64_t need_time_ns = 0;
    uint64_t logical_address = 0;
    uint64_t bytes = 0;
    std::string direction;
    std::string source_tier;
    std::string destination_tier;
    std::string client_id;
    std::string queue_id;
    uint32_t queue_depth = 1;
    uint64_t transfer_granularity_bytes = 0;
    bool can_overlap = false;
    std::string dependency_ids;
    std::string provenance = "synthetic";
};

struct ServiceEvent {
    int schema_version = 1;
    std::string event_id;
    std::string endpoint_id;
    std::string direction;
    uint64_t issue_time_ns = 0;
    double service_start_ns = 0.0;
    double service_end_ns = 0.0;
    double queue_delay_ns = 0.0;
    double base_latency_ns = 0.0;
    double media_latency_ns = 0.0;
    double topology_latency_ns = 0.0;
    double bandwidth_delay_ns = 0.0;
    double congestion_delay_ns = 0.0;
    double total_service_time_ns = 0.0;
    double effective_bandwidth_gib_s = 0.0;
    uint64_t requested_bytes = 0;
    uint64_t modeled_bytes = 0;
    bool capacity_hit = true;
    std::string model_mode;
    std::string model_assumptions;
    std::string provenance = "modeled";
};

HardwareProfile loadHardwareProfile(const std::string& path);
std::vector<TransferRequest> loadTransferRequestsCsv(const std::string& path);
void writeServiceEventsCsv(const std::string& path, const std::vector<ServiceEvent>& events);
void writeReplayMetadataJson(const std::string& path, const HardwareProfile& profile,
                             const std::vector<ServiceEvent>& events, ReplayMode mode);

class TensorTransferModel {
public:
    explicit TensorTransferModel(HardwareProfile profile);

    std::vector<ServiceEvent> replay(const std::vector<TransferRequest>& requests,
                                     ReplayMode mode = ReplayMode::Auto) const;

private:
    HardwareProfile profile_;
};

ReplayMode parseReplayMode(const std::string& value);
std::string replayModeName(ReplayMode mode);

}  // namespace cxlmemsim::llm

#endif  // CXLMEMSIM_LLM_TRANSFER_MODEL_H
