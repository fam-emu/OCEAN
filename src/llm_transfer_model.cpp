#include "llm_transfer_model.h"
#include "llm_bulk_core.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

namespace cxlmemsim::llm {
namespace {

constexpr double kGib = 1024.0 * 1024.0 * 1024.0;

std::string trim(const std::string &value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
        return "";
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::string unquote(const std::string &value) {
    const auto cleaned = trim(value);
    if (cleaned.size() >= 2 &&
        ((cleaned.front() == '"' && cleaned.back() == '"') || (cleaned.front() == '\'' && cleaned.back() == '\''))) {
        return cleaned.substr(1, cleaned.size() - 2);
    }
    return cleaned;
}

std::vector<std::string> parseCsvRow(const std::string &line) {
    std::vector<std::string> fields;
    std::string current;
    bool quoted = false;
    for (size_t index = 0; index < line.size(); ++index) {
        const char ch = line[index];
        if (ch == '"') {
            if (quoted && index + 1 < line.size() && line[index + 1] == '"') {
                current.push_back('"');
                ++index;
            } else {
                quoted = !quoted;
            }
        } else if (ch == ',' && !quoted) {
            fields.push_back(current);
            current.clear();
        } else {
            current.push_back(ch);
        }
    }
    if (quoted)
        throw std::runtime_error("unterminated quoted CSV field");
    fields.push_back(current);
    return fields;
}

std::string csvEscape(const std::string &value) {
    if (value.find_first_of(",\"\r\n") == std::string::npos)
        return value;
    std::string escaped = "\"";
    for (const char ch : value)
        escaped += ch == '"' ? "\"\"" : std::string(1, ch);
    escaped += '"';
    return escaped;
}

uint64_t parseUnsigned(const std::string &value, const std::string &field) {
    try {
        size_t consumed = 0;
        const auto parsed = std::stoull(trim(value), &consumed, 0);
        if (consumed != trim(value).size())
            throw std::invalid_argument("suffix");
        return parsed;
    } catch (const std::exception &) {
        throw std::runtime_error("invalid unsigned integer for " + field + ": " + value);
    }
}

int64_t parseSigned(const std::string &value, const std::string &field) {
    try {
        size_t consumed = 0;
        const auto parsed = std::stoll(trim(value), &consumed, 0);
        if (consumed != trim(value).size())
            throw std::invalid_argument("suffix");
        return parsed;
    } catch (const std::exception &) {
        throw std::runtime_error("invalid signed integer for " + field + ": " + value);
    }
}

double parseDouble(const std::string &value, const std::string &field) {
    try {
        size_t consumed = 0;
        const auto parsed = std::stod(trim(value), &consumed);
        if (consumed != trim(value).size())
            throw std::invalid_argument("suffix");
        return parsed;
    } catch (const std::exception &) {
        throw std::runtime_error("invalid number for " + field + ": " + value);
    }
}

bool parseBool(const std::string &value, const std::string &field) {
    auto lower = trim(value);
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (lower == "true" || lower == "1" || lower == "yes")
        return true;
    if (lower == "false" || lower == "0" || lower == "no" || lower.empty())
        return false;
    throw std::runtime_error("invalid boolean for " + field + ": " + value);
}

uint64_t roundUp(uint64_t value, uint64_t granularity) {
    if (granularity == 0)
        return value;
    if (value > std::numeric_limits<uint64_t>::max() - (granularity - 1)) {
        throw std::overflow_error("transfer size overflows granularity rounding");
    }
    return ((value + granularity - 1) / granularity) * granularity;
}

double transferTimeNs(uint64_t bytes, double bandwidth_gib_s) {
    if (!(bandwidth_gib_s > 0.0))
        throw std::runtime_error("bandwidth must be positive");
    return (static_cast<double>(bytes) / (bandwidth_gib_s * kGib)) * 1.0e9;
}

bool isReadDirection(const TransferRequest &request) {
    return request.source_tier == "CXL_MEMORY" || request.source_tier == "REMOTE_NUMA" ||
           request.direction == "REMOTE_TO_GPU" || request.direction == "REMOTE_TO_LOCAL";
}

std::vector<std::string> parseDependencyIds(const std::string &value) {
    std::vector<std::string> result;
    std::string current;
    bool quoted = false;
    bool escaped = false;
    for (const char ch : trim(value)) {
        if (escaped) {
            current.push_back(ch);
            escaped = false;
        } else if (ch == '\\' && quoted) {
            escaped = true;
        } else if (ch == '"') {
            quoted = !quoted;
        } else if (!quoted && (ch == ',' || ch == ';' || ch == '|' || ch == '[' || ch == ']')) {
            const auto dependency = trim(current);
            if (!dependency.empty())
                result.push_back(dependency);
            current.clear();
        } else {
            current.push_back(ch);
        }
    }
    if (quoted || escaped)
        throw std::runtime_error("invalid dependency_ids encoding: " + value);
    const auto dependency = trim(current);
    if (!dependency.empty())
        result.push_back(dependency);
    return result;
}

std::map<std::string, std::string> parseSimpleYaml(const std::string &path) {
    std::ifstream input(path);
    if (!input)
        throw std::runtime_error("cannot open hardware profile: " + path);
    std::map<std::string, std::string> values;
    std::vector<std::pair<int, std::string>> parents;
    std::string raw;
    size_t line_number = 0;
    while (std::getline(input, raw)) {
        ++line_number;
        const auto comment = raw.find('#');
        if (comment != std::string::npos)
            raw.erase(comment);
        if (trim(raw).empty())
            continue;
        const int indent = static_cast<int>(raw.find_first_not_of(' '));
        const auto colon = raw.find(':');
        if (colon == std::string::npos) {
            throw std::runtime_error("hardware profile line " + std::to_string(line_number) + " has no ':'");
        }
        const auto key = trim(raw.substr(0, colon));
        const auto value = unquote(raw.substr(colon + 1));
        while (!parents.empty() && parents.back().first >= indent)
            parents.pop_back();
        std::string qualified;
        for (const auto &[unused, parent] : parents) {
            (void)unused;
            if (!qualified.empty())
                qualified += '.';
            qualified += parent;
        }
        if (!qualified.empty())
            qualified += '.';
        qualified += key;
        if (value.empty()) {
            parents.emplace_back(indent, key);
        } else {
            values[qualified] = value;
            values[key] = value; // Flat profiles remain supported.
        }
    }
    return values;
}

std::string lookup(const std::map<std::string, std::string> &values, std::initializer_list<const char *> keys,
                   const std::string &fallback) {
    for (const auto *key : keys) {
        const auto found = values.find(key);
        if (found != values.end())
            return found->second;
    }
    return fallback;
}

uint64_t getU64(const std::map<std::string, std::string> &values, std::initializer_list<const char *> keys,
                uint64_t fallback) {
    const auto value = lookup(values, keys, "");
    return value.empty() ? fallback : parseUnsigned(value, *keys.begin());
}

double getDouble(const std::map<std::string, std::string> &values, std::initializer_list<const char *> keys,
                 double fallback) {
    const auto value = lookup(values, keys, "");
    return value.empty() ? fallback : parseDouble(value, *keys.begin());
}

std::string jsonEscape(const std::string &value) {
    std::ostringstream out;
    for (const char ch : value) {
        switch (ch) {
        case '\\':
            out << "\\\\";
            break;
        case '"':
            out << "\\\"";
            break;
        case '\n':
            out << "\\n";
            break;
        case '\r':
            out << "\\r";
            break;
        case '\t':
            out << "\\t";
            break;
        default:
            out << ch;
        }
    }
    return out.str();
}

std::string fileFingerprint(const std::string &path) {
    std::ifstream input(path, std::ios::binary);
    if (!input)
        throw std::runtime_error("cannot hash input file: " + path);
    uint64_t hash = 1469598103934665603ULL;
    char buffer[8192];
    while (input) {
        input.read(buffer, sizeof(buffer));
        for (std::streamsize index = 0; index < input.gcount(); ++index) {
            hash ^= static_cast<unsigned char>(buffer[index]);
            hash *= 1099511628211ULL;
        }
    }
    std::ostringstream out;
    out << "fnv1a64:" << std::hex << std::setfill('0') << std::setw(16) << hash;
    return out.str();
}

uint32_t stableIdentifier(const std::string &value) {
    uint32_t hash = 2166136261U;
    for (const unsigned char ch : value) {
        hash ^= ch;
        hash *= 16777619U;
    }
    return hash;
}

} // namespace

ReplayMode parseReplayMode(const std::string &value) {
    if (value == "auto")
        return ReplayMode::Auto;
    if (value == "aggregate")
        return ReplayMode::Aggregate;
    if (value == "detailed")
        return ReplayMode::Detailed;
    throw std::runtime_error("unsupported replay mode: " + value);
}

std::string replayModeName(ReplayMode mode) {
    if (mode == ReplayMode::Aggregate)
        return "aggregate";
    if (mode == ReplayMode::Detailed)
        return "detailed";
    return "auto";
}

ReplayBackend parseReplayBackend(const std::string &value) {
    if (value == "analytical")
        return ReplayBackend::Analytical;
    if (value == "cxlmemsim-core")
        return ReplayBackend::CXLMemSimCore;
    throw std::runtime_error("unsupported replay backend: " + value);
}

std::string replayBackendName(ReplayBackend backend) {
    return backend == ReplayBackend::CXLMemSimCore ? "cxlmemsim-core" : "analytical";
}

HardwareProfile loadHardwareProfile(const std::string &path) {
    const auto values = parseSimpleYaml(path);
    HardwareProfile profile;
    profile.schema_version = static_cast<int>(getU64(values, {"schema_version"}, 1));
    profile.profile_name = lookup(values, {"profile_name", "name"}, profile.profile_name);
    profile.path_mode = lookup(values, {"path.mode", "path_mode"}, profile.path_mode);
    profile.base_latency_ns = getDouble(values, {"latency.base_ns", "base_latency_ns"}, profile.base_latency_ns);
    profile.media_latency_ns = getDouble(values, {"latency.media_ns", "media_latency_ns"}, profile.media_latency_ns);
    profile.read_bandwidth_gib_s =
        getDouble(values, {"bandwidth.read_gib_s", "read_bandwidth_gib_s"}, profile.read_bandwidth_gib_s);
    profile.write_bandwidth_gib_s =
        getDouble(values, {"bandwidth.write_gib_s", "write_bandwidth_gib_s"}, profile.write_bandwidth_gib_s);
    const double capacity_gib = getDouble(values, {"capacity_gib"}, 0.0);
    profile.capacity_bytes =
        getU64(values, {"capacity_bytes"},
               capacity_gib > 0.0 ? static_cast<uint64_t>(capacity_gib * kGib) : profile.capacity_bytes);
    profile.num_expanders =
        static_cast<uint32_t>(getU64(values, {"topology.num_expanders", "num_expanders"}, profile.num_expanders));
    profile.switch_hops =
        static_cast<uint32_t>(getU64(values, {"topology.switch_hops", "switch_hops"}, profile.switch_hops));
    profile.per_hop_latency_ns =
        getDouble(values, {"topology.per_hop_latency_ns", "per_hop_latency_ns"}, profile.per_hop_latency_ns);
    profile.num_ports = static_cast<uint32_t>(getU64(values, {"topology.num_ports", "num_ports"}, profile.num_ports));
    profile.num_clients =
        static_cast<uint32_t>(getU64(values, {"queue.num_clients", "num_clients"}, profile.num_clients));
    profile.queue_depth = static_cast<uint32_t>(getU64(values, {"queue.depth", "queue_depth"}, profile.queue_depth));
    profile.max_outstanding_requests = static_cast<uint32_t>(getU64(
        values, {"queue.max_outstanding_requests", "max_outstanding_requests"}, profile.max_outstanding_requests));
    profile.congestion_model = lookup(values, {"queue.congestion_model", "congestion_model"}, profile.congestion_model);
    profile.transfer_granularity_bytes =
        getU64(values, {"transfer_granularity_bytes"}, profile.transfer_granularity_bytes);
    profile.gpu_link_bandwidth_gib_s =
        getDouble(values, {"bandwidth.gpu_link_gib_s", "gpu_link_bandwidth_gib_s"}, profile.gpu_link_bandwidth_gib_s);
    profile.local_dram_bandwidth_gib_s = getDouble(values, {"bandwidth.local_dram_gib_s", "local_dram_bandwidth_gib_s"},
                                                   profile.local_dram_bandwidth_gib_s);
    profile.local_to_gpu_latency_ns =
        getDouble(values, {"latency.local_to_gpu_ns", "local_to_gpu_latency_ns"}, profile.local_to_gpu_latency_ns);
    profile.detailed_threshold_bytes = getU64(values, {"detailed_threshold_bytes"}, profile.detailed_threshold_bytes);

    if (profile.schema_version != 1)
        throw std::runtime_error("unsupported hardware profile schema_version");
    if (profile.path_mode != "direct_dma" && profile.path_mode != "staged_copy") {
        throw std::runtime_error("path mode must be direct_dma or staged_copy");
    }
    if (profile.num_expanders == 0 || profile.num_ports == 0 || profile.queue_depth == 0 ||
        profile.max_outstanding_requests == 0 || profile.capacity_bytes == 0 ||
        profile.transfer_granularity_bytes == 0) {
        throw std::runtime_error("capacity, topology, queue, and granularity values must be positive");
    }
    if (profile.congestion_model != "fifo" && profile.congestion_model != "none") {
        throw std::runtime_error("congestion_model must be fifo or none");
    }
    return profile;
}

std::vector<TransferRequest> loadTransferRequestsCsv(const std::string &path) {
    std::ifstream input(path);
    if (!input)
        throw std::runtime_error("cannot open transfer trace: " + path);
    std::string line;
    if (!std::getline(input, line))
        throw std::runtime_error("transfer trace is empty");
    const auto header = parseCsvRow(line);
    std::unordered_map<std::string, size_t> columns;
    for (size_t index = 0; index < header.size(); ++index)
        columns[trim(header[index])] = index;
    const std::vector<std::string> required = {"schema_version",
                                               "event_id",
                                               "request_id",
                                               "object_id",
                                               "object_type",
                                               "data_type",
                                               "phase",
                                               "layer_id",
                                               "issue_time_ns",
                                               "need_time_ns",
                                               "logical_address",
                                               "bytes",
                                               "direction",
                                               "source_tier",
                                               "destination_tier",
                                               "client_id",
                                               "queue_id",
                                               "queue_depth",
                                               "transfer_granularity_bytes",
                                               "can_overlap",
                                               "dependency_ids"};
    for (const auto &field : required) {
        if (!columns.contains(field))
            throw std::runtime_error("transfer trace missing column: " + field);
    }
    auto get = [&](const std::vector<std::string> &row, const std::string &field) -> std::string {
        const auto index = columns.at(field);
        return index < row.size() ? trim(row[index]) : "";
    };

    std::vector<TransferRequest> requests;
    size_t line_number = 1;
    while (std::getline(input, line)) {
        ++line_number;
        if (trim(line).empty())
            continue;
        const auto row = parseCsvRow(line);
        try {
            TransferRequest request;
            request.schema_version = static_cast<int>(parseUnsigned(get(row, "schema_version"), "schema_version"));
            request.event_id = get(row, "event_id");
            request.request_id = get(row, "request_id");
            request.object_id = get(row, "object_id");
            request.object_type = get(row, "object_type");
            request.data_type = get(row, "data_type");
            request.phase = get(row, "phase");
            request.layer_id = parseSigned(get(row, "layer_id"), "layer_id");
            request.issue_time_ns = parseUnsigned(get(row, "issue_time_ns"), "issue_time_ns");
            request.need_time_ns = parseUnsigned(get(row, "need_time_ns"), "need_time_ns");
            request.logical_address = parseUnsigned(get(row, "logical_address"), "logical_address");
            request.bytes = parseUnsigned(get(row, "bytes"), "bytes");
            request.direction = get(row, "direction");
            request.source_tier = get(row, "source_tier");
            request.destination_tier = get(row, "destination_tier");
            request.client_id = get(row, "client_id");
            request.queue_id = get(row, "queue_id");
            request.queue_depth = static_cast<uint32_t>(parseUnsigned(get(row, "queue_depth"), "queue_depth"));
            request.transfer_granularity_bytes =
                parseUnsigned(get(row, "transfer_granularity_bytes"), "transfer_granularity_bytes");
            request.can_overlap = parseBool(get(row, "can_overlap"), "can_overlap");
            request.dependency_ids = get(row, "dependency_ids");
            if (columns.contains("provenance"))
                request.provenance = get(row, "provenance");
            if (request.schema_version != 1 || request.event_id.empty() || request.object_id.empty() ||
                request.bytes == 0 || request.need_time_ns < request.issue_time_ns) {
                throw std::runtime_error("invalid schema, identifier, byte count, or time order");
            }
            requests.push_back(std::move(request));
        } catch (const std::exception &error) {
            throw std::runtime_error("transfer trace line " + std::to_string(line_number) + ": " + error.what());
        }
    }
    if (requests.empty())
        throw std::runtime_error("transfer trace has no requests");
    return requests;
}

TensorTransferModel::TensorTransferModel(HardwareProfile profile) : profile_(std::move(profile)) {}

std::vector<ServiceEvent> TensorTransferModel::replay(const std::vector<TransferRequest> &requests, ReplayMode mode,
                                                      ReplayBackend backend) const {
    std::vector<TransferRequest> ordered = requests;
    std::stable_sort(ordered.begin(), ordered.end(), [](const auto &left, const auto &right) {
        return std::tie(left.issue_time_ns, left.event_id) < std::tie(right.issue_time_ns, right.event_id);
    });
    evidence_ = {};
    if (backend == ReplayBackend::CXLMemSimCore) {
        bulk::BulkCoreProfile core_profile;
        core_profile.path_mode = profile_.path_mode;
        core_profile.base_latency_ns = profile_.base_latency_ns;
        core_profile.media_latency_ns = profile_.media_latency_ns;
        core_profile.topology_latency_ns = profile_.switch_hops * profile_.per_hop_latency_ns;
        core_profile.read_bandwidth_gib_s = profile_.read_bandwidth_gib_s;
        core_profile.write_bandwidth_gib_s = profile_.write_bandwidth_gib_s;
        core_profile.gpu_link_bandwidth_gib_s = profile_.gpu_link_bandwidth_gib_s;
        core_profile.local_dram_bandwidth_gib_s = profile_.local_dram_bandwidth_gib_s;
        core_profile.local_to_gpu_latency_ns = profile_.local_to_gpu_latency_ns;
        core_profile.capacity_bytes = profile_.capacity_bytes;
        core_profile.num_expanders = profile_.num_expanders;
        core_profile.num_ports = profile_.num_ports;
        core_profile.max_outstanding_requests = profile_.max_outstanding_requests;
        core_profile.congestion_model = profile_.congestion_model;
        bulk::CXLMemSimBulkController controller(core_profile);
        std::unordered_map<std::string, double> dependency_ready;
        std::vector<ServiceEvent> core_result;
        core_result.reserve(ordered.size());

        for (const auto &request : ordered) {
            const uint64_t granularity = request.transfer_granularity_bytes > 0 ? request.transfer_granularity_bytes
                                                                                : profile_.transfer_granularity_bytes;
            const uint64_t modeled_bytes = roundUp(request.bytes, granularity);
            ReplayMode selected_mode = mode;
            if (selected_mode == ReplayMode::Auto) {
                selected_mode =
                    modeled_bytes <= profile_.detailed_threshold_bytes ? ReplayMode::Detailed : ReplayMode::Aggregate;
            }
            double required_ready = static_cast<double>(request.issue_time_ns);
            for (const auto &dependency : parseDependencyIds(request.dependency_ids)) {
                const auto found = dependency_ready.find(dependency);
                if (found == dependency_ready.end()) {
                    throw std::runtime_error("transfer " + request.event_id +
                                             " has an unresolved dependency: " + dependency);
                }
                required_ready = std::max(required_ready, found->second);
            }

            bulk::BulkMemoryRequest bulk_request;
            bulk_request.event_id = request.event_id;
            bulk_request.issue_time_ns = request.issue_time_ns;
            bulk_request.need_time_ns = request.need_time_ns;
            bulk_request.logical_address = request.logical_address;
            bulk_request.size_bytes = request.bytes;
            bulk_request.is_write = !isReadDirection(request);
            bulk_request.client_id = stableIdentifier(request.client_id);
            bulk_request.queue_id = stableIdentifier(request.queue_id);
            bulk_request.transfer_granularity_bytes = granularity;
            bulk_request.chunk_bytes = selected_mode == ReplayMode::Detailed ? granularity : modeled_bytes;
            bulk_request.dependency_ready_ns = required_ready;
            const auto completion = controller.service(bulk_request);

            ServiceEvent event;
            event.event_id = request.event_id;
            event.endpoint_id =
                "expander-" + std::to_string(completion.endpoint_id) + "-port-" + std::to_string(completion.port_id);
            event.port_id = completion.port_id;
            event.direction = request.direction;
            event.issue_time_ns = request.issue_time_ns;
            event.service_start_ns = completion.service_start_ns;
            event.service_end_ns = completion.service_end_ns;
            event.queue_delay_ns = completion.queue_delay_ns;
            event.base_latency_ns = completion.base_delay_ns;
            event.media_latency_ns = completion.media_delay_ns;
            event.topology_latency_ns = completion.topology_delay_ns;
            event.bandwidth_delay_ns = completion.bandwidth_delay_ns;
            event.congestion_delay_ns = completion.congestion_delay_ns;
            event.total_service_time_ns = completion.service_end_ns - request.issue_time_ns;
            event.effective_bandwidth_gib_s = completion.effective_bandwidth_gib_s;
            event.requested_bytes = completion.requested_bytes;
            event.modeled_bytes = completion.modeled_bytes;
            event.chunk_count = completion.chunk_count;
            event.capacity_hit = completion.capacity_hit;
            event.backend = replayBackendName(backend);
            event.model_mode = replayModeName(selected_mode);
            event.model_assumptions = profile_.path_mode +
                                      ";controller_hdm_expander_bulk_path;critical_chunk_decomposition;"
                                      "per_expander_port_fifo;transfer_dependencies_enforced;"
                                      "no_cache_or_coherency";
            dependency_ready[request.event_id] = event.service_end_ns;
            core_result.push_back(std::move(event));
        }
        const auto counters = controller.debugCounters();
        evidence_.backend_implementation = "CXLMemSimBulkController::service->RegionDecoder::decode(HDM)->"
                                           "CXLMemSimBulkExpander::serviceChunk";
        evidence_.effective_topology = controller.effectiveTopology();
        evidence_.controller_service_calls = counters.controller_service_calls;
        evidence_.hdm_decode_calls = counters.hdm_decode_calls;
        evidence_.expander_service_calls = counters.expander_service_calls;
        for (const auto &event : core_result)
            evidence_.chunk_count += event.chunk_count;
        return core_result;
    }

    const size_t lanes_per_expander =
        std::max<size_t>(1, std::min<uint32_t>(profile_.num_ports, profile_.max_outstanding_requests));
    std::vector<std::vector<double>> lane_ready(profile_.num_expanders, std::vector<double>(lanes_per_expander, 0.0));
    std::vector<ServiceEvent> result;
    result.reserve(ordered.size());
    std::unordered_map<std::string, double> dependency_ready;

    for (const auto &request : ordered) {
        const uint64_t granularity = request.transfer_granularity_bytes > 0 ? request.transfer_granularity_bytes
                                                                            : profile_.transfer_granularity_bytes;
        const uint64_t modeled_bytes = roundUp(request.bytes, granularity);
        const uint32_t endpoint =
            static_cast<uint32_t>((request.logical_address / granularity) % profile_.num_expanders);
        size_t lane = 0;
        for (size_t candidate = 1; candidate < lanes_per_expander; ++candidate) {
            if (lane_ready[endpoint][candidate] < lane_ready[endpoint][lane])
                lane = candidate;
        }

        const bool read = isReadDirection(request);
        const double cxl_bandwidth = read ? profile_.read_bandwidth_gib_s : profile_.write_bandwidth_gib_s;
        const double topology_latency = profile_.switch_hops * profile_.per_hop_latency_ns;
        double bandwidth_delay = 0.0;
        double effective_bandwidth = 0.0;
        double extra_staged_latency = 0.0;
        if (profile_.path_mode == "direct_dma") {
            effective_bandwidth = std::min(cxl_bandwidth, profile_.gpu_link_bandwidth_gib_s);
            bandwidth_delay = transferTimeNs(modeled_bytes, effective_bandwidth);
        } else {
            const double cxl_to_local = std::min(cxl_bandwidth, profile_.local_dram_bandwidth_gib_s);
            const double local_to_gpu =
                std::min(profile_.local_dram_bandwidth_gib_s, profile_.gpu_link_bandwidth_gib_s);
            const double first_leg = transferTimeNs(modeled_bytes, cxl_to_local);
            const double second_leg = transferTimeNs(modeled_bytes, local_to_gpu);
            bandwidth_delay = first_leg + second_leg;
            extra_staged_latency = profile_.local_to_gpu_latency_ns;
            effective_bandwidth = (static_cast<double>(modeled_bytes) / kGib) / (bandwidth_delay / 1.0e9);
        }

        ReplayMode selected_mode = mode;
        if (selected_mode == ReplayMode::Auto) {
            selected_mode =
                modeled_bytes <= profile_.detailed_threshold_bytes ? ReplayMode::Detailed : ReplayMode::Aggregate;
        }
        double required_ready = static_cast<double>(request.issue_time_ns);
        for (const auto &dependency : parseDependencyIds(request.dependency_ids)) {
            const auto found = dependency_ready.find(dependency);
            if (found == dependency_ready.end()) {
                throw std::runtime_error("transfer " + request.event_id +
                                         " has an unresolved dependency: " + dependency);
            }
            required_ready = std::max(required_ready, found->second);
        }
        const double service_start =
            profile_.congestion_model == "none" ? required_ready : std::max(required_ready, lane_ready[endpoint][lane]);
        const double queue_delay = service_start - request.issue_time_ns;
        const double service_only = profile_.base_latency_ns + profile_.media_latency_ns + topology_latency +
                                    extra_staged_latency + bandwidth_delay;
        const double service_end = service_start + service_only;
        if (profile_.congestion_model != "none")
            lane_ready[endpoint][lane] = service_end;

        const bool address_overflow =
            request.logical_address > profile_.capacity_bytes ||
            modeled_bytes > profile_.capacity_bytes - std::min(request.logical_address, profile_.capacity_bytes);
        ServiceEvent event;
        event.event_id = request.event_id;
        event.endpoint_id = "expander-" + std::to_string(endpoint) + "-port-" + std::to_string(lane);
        event.port_id = static_cast<uint32_t>(lane);
        event.direction = request.direction;
        event.issue_time_ns = request.issue_time_ns;
        event.service_start_ns = service_start;
        event.service_end_ns = service_end;
        event.queue_delay_ns = queue_delay;
        event.base_latency_ns = profile_.base_latency_ns + extra_staged_latency;
        event.media_latency_ns = profile_.media_latency_ns;
        event.topology_latency_ns = topology_latency;
        event.bandwidth_delay_ns = bandwidth_delay;
        event.congestion_delay_ns = 0.0;
        event.total_service_time_ns = service_end - request.issue_time_ns;
        event.effective_bandwidth_gib_s = effective_bandwidth;
        event.requested_bytes = request.bytes;
        event.modeled_bytes = modeled_bytes;
        event.chunk_count = 1;
        event.capacity_hit = !address_overflow;
        event.backend = replayBackendName(backend);
        event.model_mode = replayModeName(selected_mode);
        event.model_assumptions = profile_.path_mode +
                                  ";analytical_granularity_fast_path;one_fixed_latency_per_bulk_request;"
                                  "bandwidth_serialized_per_port;transfer_dependencies_enforced;"
                                  "queue_delay_represents_contention;no_cache_or_coherency";
        dependency_ready[request.event_id] = service_end;
        result.push_back(std::move(event));
    }
    evidence_.backend_implementation = "TensorTransferModel::replay analytical closed-form";
    evidence_.effective_topology = "analytical address striping with per-expander/per-port FIFO";
    evidence_.chunk_count = result.size();
    return result;
}

void writeServiceEventsCsv(const std::string &path, const std::vector<ServiceEvent> &events) {
    std::ofstream output(path);
    if (!output)
        throw std::runtime_error("cannot create service event output: " + path);
    output << "schema_version,event_id,endpoint_id,port_id,direction,issue_time_"
              "ns,service_start_ns,service_end_ns,"
              "queue_delay_ns,base_latency_ns,media_latency_ns,topology_latency_"
              "ns,bandwidth_delay_ns,"
              "congestion_delay_ns,total_service_time_ns,effective_bandwidth_gib_"
              "s,requested_bytes,"
              "modeled_bytes,chunk_count,capacity_hit,backend,model_mode,model_"
              "assumptions,provenance\n";
    output << std::setprecision(17);
    for (const auto &event : events) {
        output << event.schema_version << ',' << csvEscape(event.event_id) << ',' << csvEscape(event.endpoint_id) << ','
               << event.port_id << ',' << csvEscape(event.direction) << ',' << event.issue_time_ns << ','
               << event.service_start_ns << ',' << event.service_end_ns << ',' << event.queue_delay_ns << ','
               << event.base_latency_ns << ',' << event.media_latency_ns << ',' << event.topology_latency_ns << ','
               << event.bandwidth_delay_ns << ',' << event.congestion_delay_ns << ',' << event.total_service_time_ns
               << ',' << event.effective_bandwidth_gib_s << ',' << event.requested_bytes << ',' << event.modeled_bytes
               << ',' << event.chunk_count << ',' << (event.capacity_hit ? "true" : "false") << ','
               << csvEscape(event.backend) << ',' << csvEscape(event.model_mode) << ','
               << csvEscape(event.model_assumptions) << ',' << event.provenance << '\n';
    }
}

void writeReplayMetadataJson(const std::string &path, const HardwareProfile &profile,
                             const std::vector<ServiceEvent> &events, ReplayMode mode, ReplayBackend backend,
                             const ReplayEvidence &evidence, const std::string &hardware_profile_path,
                             const std::string &input_trace_path) {
    std::ofstream output(path);
    if (!output)
        throw std::runtime_error("cannot create replay metadata: " + path);
    uint64_t requested = 0;
    uint64_t modeled = 0;
    double queue = 0.0;
    double service = 0.0;
    size_t capacity_misses = 0;
    for (const auto &event : events) {
        requested += event.requested_bytes;
        modeled += event.modeled_bytes;
        queue += event.queue_delay_ns;
        service += event.total_service_time_ns;
        if (!event.capacity_hit)
            ++capacity_misses;
    }
    output << std::setprecision(17) << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"backend\": \"" << replayBackendName(backend) << "\",\n"
           << "  \"backend_implementation\": \"" << jsonEscape(evidence.backend_implementation) << "\",\n"
           << "  \"hardware_profile_hash\": \"" << fileFingerprint(hardware_profile_path) << "\",\n"
           << "  \"input_trace_hash\": \"" << fileFingerprint(input_trace_path) << "\",\n"
           << "  \"profile_name\": \"" << jsonEscape(profile.profile_name) << "\",\n"
           << "  \"path_mode\": \"" << jsonEscape(profile.path_mode) << "\",\n"
           << "  \"requested_mode\": \"" << replayModeName(mode) << "\",\n"
           << "  \"effective_topology\": \"" << jsonEscape(evidence.effective_topology) << "\",\n"
           << "  \"transfer_granularity_bytes\": " << profile.transfer_granularity_bytes << ",\n"
           << "  \"controller_service_calls\": " << evidence.controller_service_calls << ",\n"
           << "  \"hdm_decode_calls\": " << evidence.hdm_decode_calls << ",\n"
           << "  \"expander_service_calls\": " << evidence.expander_service_calls << ",\n"
           << "  \"chunk_count\": " << evidence.chunk_count << ",\n"
           << "  \"event_count\": " << events.size() << ",\n"
           << "  \"requested_bytes\": " << requested << ",\n"
           << "  \"modeled_bytes\": " << modeled << ",\n"
           << "  \"queue_delay_ns\": " << queue << ",\n"
           << "  \"total_service_time_ns\": " << service << ",\n"
           << "  \"capacity_misses\": " << capacity_misses << ",\n"
           << "  \"provenance\": \"modeled\",\n"
           << "  \"unsupported_mechanisms\": [\"shared-switch bandwidth pool\", "
              "\"credit back-pressure\", \"cache/coherency\", \"cycle accuracy\"],\n"
           << "  \"qemu_required\": false,\n"
           << "  \"tcp_required\": false\n"
           << "}\n";
}

} // namespace cxlmemsim::llm
