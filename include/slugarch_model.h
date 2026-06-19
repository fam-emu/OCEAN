/*
 * LegoMem SlugArch replay boundary model.
 *
 * The model captures memory-server-visible endpoint events and records enough
 * metadata to validate replay order, labels, and epoch seals.
 */

#ifndef LEGOMEM_SLUGARCH_MODEL_H
#define LEGOMEM_SLUGARCH_MODEL_H

#include <cstdint>
#include <set>
#include <string>
#include <vector>

enum class SlugArchEventClass {
    Read,
    Write,
    Atomic,
    Ownership,
    Invalidate,
    Flush,
    Dma,
    Io,
    Interrupt,
    Map,
    Unmap,
    Migrate,
    Poison,
    Input,
    Failure,
    Fence,
    Epoch,
    Seal
};

enum class SlugArchRecordMode {
    Full,
    Delta,
    Validation,
    OrderingOnly
};

struct SlugArchBoundaryEvent {
    SlugArchEventClass cls;
    uint64_t src;
    uint64_t dst;
    uint64_t epoch;
    uint64_t object;
    uint64_t size;
    std::string label;
};

struct SlugArchRecordOptions {
    SlugArchRecordMode mode = SlugArchRecordMode::Validation;
    std::string payload;
    std::string ordering;
};

struct SlugArchReplayRecord {
    uint64_t id;
    SlugArchBoundaryEvent event;
    std::vector<uint64_t> deps;
    SlugArchRecordMode mode;
    std::string payload;
    std::string ordering;
    std::string commitment;
};

struct SlugArchEpochSeal {
    uint64_t epoch;
    uint64_t record_count;
    std::string digest;
};

class SlugArchReplayModel {
public:
    void begin_epoch(uint64_t epoch);
    SlugArchReplayRecord record(const SlugArchBoundaryEvent& event,
                                std::vector<uint64_t> deps = {},
                                const SlugArchRecordOptions& options = {});
    SlugArchEpochSeal seal_epoch() const;

    bool matches(const SlugArchBoundaryEvent& event,
                 const SlugArchReplayRecord& record,
                 const std::string& observed_payload = "") const;
    bool is_dependency_satisfied(const SlugArchReplayRecord& record,
                                 const std::set<uint64_t>& consumed) const;

private:
    uint64_t current_epoch_ = 0;
    uint64_t next_id_ = 1;
    std::vector<SlugArchReplayRecord> records_;

    static std::string commitment_for(const SlugArchBoundaryEvent& event,
                                      SlugArchRecordMode mode,
                                      const std::string& payload,
                                      const std::string& ordering);
};

#endif
