#include "llm_bulk_core.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace cxlmemsim::bulk {
namespace {

constexpr double kGib = 1024.0 * 1024.0 * 1024.0;

uint64_t roundUp(uint64_t value, uint64_t granularity) {
    if (granularity == 0)
        throw std::runtime_error("bulk transfer granularity must be positive");
    if (value > std::numeric_limits<uint64_t>::max() - (granularity - 1)) {
        throw std::overflow_error("bulk transfer size overflows granularity rounding");
    }
    return ((value + granularity - 1) / granularity) * granularity;
}

double transferTimeNs(uint64_t bytes, double bandwidth_gib_s) {
    if (!(bandwidth_gib_s > 0.0))
        throw std::runtime_error("bulk bandwidth must be positive");
    return static_cast<double>(bytes) / (bandwidth_gib_s * kGib) * 1.0e9;
}

} // namespace

CXLMemSimBulkExpander::CXLMemSimBulkExpander(uint32_t endpoint_id, const BulkCoreProfile &profile) : profile_(profile) {
    (void)endpoint_id;
    const auto ports = std::max<uint32_t>(1, std::min(profile_.num_ports, profile_.max_outstanding_requests));
    port_ready_ns_.assign(ports, 0.0);
}

CXLMemSimBulkExpander::ChunkCompletion CXLMemSimBulkExpander::serviceChunk(double ready_time_ns, uint64_t size_bytes,
                                                                           bool is_write) {
    ++service_calls_;
    size_t port = 0;
    for (size_t candidate = 1; candidate < port_ready_ns_.size(); ++candidate) {
        if (port_ready_ns_[candidate] < port_ready_ns_[port])
            port = candidate;
    }

    const double cxl_bandwidth = is_write ? profile_.write_bandwidth_gib_s : profile_.read_bandwidth_gib_s;
    double bandwidth_delay = 0.0;
    double effective_bandwidth = 0.0;
    double staged_latency = 0.0;
    if (profile_.path_mode == "direct_dma") {
        effective_bandwidth = std::min(cxl_bandwidth, profile_.gpu_link_bandwidth_gib_s);
        bandwidth_delay = transferTimeNs(size_bytes, effective_bandwidth);
    } else if (profile_.path_mode == "staged_copy") {
        const double first_bandwidth = std::min(cxl_bandwidth, profile_.local_dram_bandwidth_gib_s);
        const double second_bandwidth =
            std::min(profile_.local_dram_bandwidth_gib_s, profile_.gpu_link_bandwidth_gib_s);
        bandwidth_delay = transferTimeNs(size_bytes, first_bandwidth) + transferTimeNs(size_bytes, second_bandwidth);
        staged_latency = profile_.local_to_gpu_latency_ns;
        effective_bandwidth = (static_cast<double>(size_bytes) / kGib) / (bandwidth_delay / 1.0e9);
    } else {
        throw std::runtime_error("unsupported bulk core path mode: " + profile_.path_mode);
    }

    const double service_start =
        profile_.congestion_model == "none" ? ready_time_ns : std::max(ready_time_ns, port_ready_ns_[port]);
    const double service_end = service_start + profile_.base_latency_ns + staged_latency + profile_.media_latency_ns +
                               profile_.topology_latency_ns + bandwidth_delay;
    if (profile_.congestion_model != "none")
        port_ready_ns_[port] = service_end;

    return {
        service_start,
        service_end,
        profile_.base_latency_ns + staged_latency,
        profile_.media_latency_ns,
        profile_.topology_latency_ns,
        bandwidth_delay,
        0.0,
        effective_bandwidth,
        static_cast<uint32_t>(port),
    };
}

CXLMemSimBulkController::CXLMemSimBulkController(BulkCoreProfile profile)
    : profile_(std::move(profile)), decoder_(HDMDecoderMode::INTERLEAVED) {
    if (profile_.capacity_bytes == 0 || profile_.num_expanders == 0 || profile_.num_ports == 0 ||
        profile_.max_outstanding_requests == 0) {
        throw std::runtime_error("bulk core capacity, expanders, ports, and "
                                 "outstanding requests must be positive");
    }
    std::vector<uint32_t> targets;
    targets.reserve(profile_.num_expanders);
    for (uint32_t endpoint = 0; endpoint < profile_.num_expanders; ++endpoint) {
        targets.push_back(endpoint);
        expanders_.push_back(std::make_unique<CXLMemSimBulkExpander>(endpoint, profile_));
    }
    decoder_.configure_interleave(InterleaveGranularity::PAGE_4K, targets, 0, profile_.capacity_bytes);
}

BulkMemoryCompletion CXLMemSimBulkController::service(const BulkMemoryRequest &request) {
    ++controller_service_calls_;
    if (request.event_id.empty() || request.size_bytes == 0 || request.transfer_granularity_bytes == 0 ||
        request.chunk_bytes == 0) {
        throw std::runtime_error("bulk core request requires identity, bytes, "
                                 "granularity, and chunk size");
    }
    if (request.need_time_ns < request.issue_time_ns) {
        throw std::runtime_error("bulk core request need time precedes issue time");
    }

    const uint64_t modeled_bytes = roundUp(request.size_bytes, request.transfer_granularity_bytes);
    const bool capacity_hit =
        request.logical_address <= profile_.capacity_bytes &&
        modeled_bytes <= profile_.capacity_bytes - std::min(request.logical_address, profile_.capacity_bytes);
    const uint64_t chunk_bytes = std::min(request.chunk_bytes, modeled_bytes);
    const uint64_t chunk_count = (modeled_bytes + chunk_bytes - 1) / chunk_bytes;
    double critical_end = -1.0;
    CXLMemSimBulkExpander::ChunkCompletion critical;
    uint32_t critical_endpoint = 0;

    uint64_t offset = 0;
    while (offset < modeled_bytes) {
        const uint64_t size = std::min(chunk_bytes, modeled_bytes - offset);
        if (request.logical_address > std::numeric_limits<uint64_t>::max() - offset) {
            throw std::overflow_error("bulk core logical address overflow");
        }
        const uint64_t address = request.logical_address + offset;
        // Preserve an explicit capacity miss while still selecting the modeled
        // endpoint that owns the wrapped interleave stripe.
        const auto decoded = decoder_.decode(address % profile_.capacity_bytes);
        ++hdm_decode_calls_;
        if (decoded.target_id == UINT32_MAX || decoded.target_id >= expanders_.size()) {
            throw std::runtime_error("HDM decoder could not route bulk request " + request.event_id);
        }
        auto completion = expanders_[decoded.target_id]->serviceChunk(
            std::max(static_cast<double>(request.issue_time_ns), request.dependency_ready_ns), size, request.is_write);
        if (completion.service_end_ns > critical_end) {
            critical_end = completion.service_end_ns;
            critical = completion;
            critical_endpoint = decoded.target_id;
        }
        offset += size;
    }

    BulkMemoryCompletion result;
    result.event_id = request.event_id;
    result.service_start_ns = critical.service_start_ns;
    result.service_end_ns = critical.service_end_ns;
    result.queue_delay_ns = critical.service_start_ns - request.issue_time_ns;
    result.topology_delay_ns = critical.topology_delay_ns;
    result.media_delay_ns = critical.media_delay_ns;
    result.bandwidth_delay_ns = critical.bandwidth_delay_ns;
    result.congestion_delay_ns = critical.congestion_delay_ns;
    result.base_delay_ns = critical.base_delay_ns;
    result.effective_bandwidth_gib_s = critical.effective_bandwidth_gib_s;
    result.requested_bytes = request.size_bytes;
    result.modeled_bytes = modeled_bytes;
    result.endpoint_id = critical_endpoint;
    result.port_id = critical.port_id;
    result.chunk_count = chunk_count;
    result.capacity_hit = capacity_hit;
    return result;
}

BulkCoreDebugCounters CXLMemSimBulkController::debugCounters() const noexcept {
    uint64_t expander_calls = 0;
    for (const auto &expander : expanders_)
        expander_calls += expander->serviceCallCount();
    return {controller_service_calls_, hdm_decode_calls_, expander_calls};
}

std::string CXLMemSimBulkController::effectiveTopology() const {
    std::ostringstream out;
    out << "HDMDecoder(INTERLEAVED)->" << profile_.num_expanders << "xCXLMemSimBulkExpander(" << profile_.num_ports
        << " ports," << profile_.topology_latency_ns << "ns topology)";
    return out.str();
}

} // namespace cxlmemsim::bulk
