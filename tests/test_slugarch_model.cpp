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
    auto second = model.record(write, {first.id});
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

    if (seal.epoch != 7 || seal.record_count != 2 || seal.digest.empty()) {
        return 4;
    }

    return 0;
}
