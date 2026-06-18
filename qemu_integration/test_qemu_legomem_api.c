#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "qemu_legomem.h"

int main(void) {
    LegoMemQemuRequest req = {0};
    LegoMemQemuResponse resp = {0};

    req.op_type = LEGOMEM_QEMU_OP_READ;
    req.region_id = LEGOMEM_QEMU_DEFAULT_REGION_ID;
    req.offset = 0x2000;
    req.size = 16;

    if (req.region_id != 1 || req.offset != 0x2000 || req.size != 16) {
        fprintf(stderr, "request fields not preserved\n");
        return 1;
    }

    memset(resp.data, 0xcd, sizeof(resp.data));
    if (resp.status != LEGOMEM_QEMU_STATUS_OK) {
        fprintf(stderr, "zero-initialized response should be OK\n");
        return 1;
    }

    return 0;
}
