#include "slugarch_model.h"

#include <functional>
#include <sstream>
#include <stdexcept>

namespace {

std::string event_class_name(SlugArchEventClass cls) {
    switch (cls) {
    case SlugArchEventClass::Read:
        return "read";
    case SlugArchEventClass::Write:
        return "write";
    case SlugArchEventClass::Atomic:
        return "atomic";
    case SlugArchEventClass::Ownership:
        return "ownership";
    case SlugArchEventClass::Invalidate:
        return "invalidate";
    case SlugArchEventClass::Flush:
        return "flush";
    case SlugArchEventClass::Dma:
        return "dma";
    case SlugArchEventClass::Io:
        return "io";
    case SlugArchEventClass::Interrupt:
        return "interrupt";
    case SlugArchEventClass::Map:
        return "map";
    case SlugArchEventClass::Unmap:
        return "unmap";
    case SlugArchEventClass::Migrate:
        return "migrate";
    case SlugArchEventClass::Poison:
        return "poison";
    case SlugArchEventClass::Input:
        return "input";
    case SlugArchEventClass::Failure:
        return "failure";
    case SlugArchEventClass::Fence:
        return "fence";
    case SlugArchEventClass::Epoch:
        return "epoch";
    case SlugArchEventClass::Seal:
        return "seal";
    }
    return "unknown";
}

std::string digest_string(const std::string& payload) {
    const auto value = std::hash<std::string>{}(payload);
    std::ostringstream out;
    out << std::hex << value;
    return out.str();
}

} // namespace

void SlugArchReplayModel::begin_epoch(uint64_t epoch) {
    current_epoch_ = epoch;
    records_.clear();
    next_id_ = 1;
}

SlugArchReplayRecord SlugArchReplayModel::record(const SlugArchBoundaryEvent& event,
                                                 std::vector<uint64_t> deps) {
    if (event.epoch != current_epoch_) {
        throw std::invalid_argument("SlugArch event epoch is not active");
    }

    SlugArchReplayRecord record{
        next_id_++,
        event,
        std::move(deps),
        commitment_for(event)
    };
    records_.push_back(record);
    return record;
}

SlugArchEpochSeal SlugArchReplayModel::seal_epoch() const {
    std::ostringstream payload;
    payload << current_epoch_;

    for (const auto& record : records_) {
        payload << '|'
                << record.id << ':'
                << record.commitment << ':'
                << record.event.label;
    }

    return SlugArchEpochSeal{
        current_epoch_,
        static_cast<uint64_t>(records_.size()),
        digest_string(payload.str())
    };
}

bool SlugArchReplayModel::matches(const SlugArchBoundaryEvent& event,
                                  const SlugArchReplayRecord& record) const {
    return event.cls == record.event.cls &&
           event.src == record.event.src &&
           event.dst == record.event.dst &&
           event.epoch == record.event.epoch &&
           event.object == record.event.object &&
           event.size == record.event.size &&
           event.label == record.event.label &&
           commitment_for(event) == record.commitment;
}

bool SlugArchReplayModel::is_dependency_satisfied(
    const SlugArchReplayRecord& record,
    const std::set<uint64_t>& consumed) const {
    for (const auto dep : record.deps) {
        if (!consumed.contains(dep)) {
            return false;
        }
    }
    return true;
}

std::string SlugArchReplayModel::commitment_for(const SlugArchBoundaryEvent& event) {
    std::ostringstream payload;
    payload << event_class_name(event.cls) << ':'
            << event.src << ':'
            << event.dst << ':'
            << event.epoch << ':'
            << event.object << ':'
            << event.size << ':'
            << event.label;
    return digest_string(payload.str());
}
