#include "slugarch_model.h"

#include <cstdint>
#include <string>
#include <vector>

int main() {
    SlugArchReplayModel model;
    model.begin_epoch(7);

    SlugArchBoundaryEvent read{
        SlugArchEventClass::Read,
        1,
        2,
        7,
        0x1000,
        64,
        "tenant-a"
    };

    SlugArchBoundaryEvent write{
        SlugArchEventClass::Write,
        2,
        1,
        7,
        0x1040,
        64,
        "tenant-a"
    };

    auto first = model.record(read);
    if (first.commitment != "91b8517698721d89") {
        return 8;
    }

    SlugArchRecordOptions full_options;
    full_options.mode = SlugArchRecordMode::Full;
    full_options.payload = "value=abcd";
    full_options.ordering = "read-before-write";

    auto second = model.record(write, {first.id}, full_options);

    SlugArchRecordOptions fence_options;
    fence_options.mode = SlugArchRecordMode::OrderingOnly;
    fence_options.ordering = "region-fence";

    SlugArchBoundaryEvent fence{
        SlugArchEventClass::Fence,
        1,
        0,
        7,
        0x1000,
        0,
        "tenant-a"
    };

    auto third = model.record(fence, {second.id}, fence_options);
    auto seal = model.seal_epoch();

    if (first.id == second.id) {
        return 1;
    }

    if (!model.is_dependency_satisfied(second, {first.id})) {
        return 2;
    }

    if (!model.matches(read, first)) {
        return 3;
    }

    if (second.mode != SlugArchRecordMode::Full ||
        second.payload != "value=abcd" ||
        second.ordering != "read-before-write") {
        return 4;
    }

    if (!model.matches(write, second, "value=abcd") ||
        model.matches(write, second, "value=dcba")) {
        return 5;
    }

    if (third.mode != SlugArchRecordMode::OrderingOnly ||
        !model.matches(fence, third)) {
        return 6;
    }

    if (seal.epoch != 7 || seal.record_count != 3 || seal.digest.empty()) {
        return 7;
    }

    return 0;
}
