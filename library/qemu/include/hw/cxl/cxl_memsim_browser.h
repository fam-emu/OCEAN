#ifndef CXL_MEMSIM_BROWSER_H
#define CXL_MEMSIM_BROWSER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

bool cxl_memsim_transport_is_browser(const char *transport);

int cxl_memsim_browser_connect(const char *device, const char *pool,
                               uint16_t port, uint64_t size);
void cxl_memsim_browser_disconnect(const char *device);

int cxl_memsim_browser_request(uint8_t op, uint64_t addr, uint64_t size,
                               const void *data, uint64_t value,
                               uint64_t expected, void *resp,
                               size_t resp_size);

bool cxl_memsim_browser_read(uint64_t addr, void *data, size_t size);
bool cxl_memsim_browser_write(uint64_t addr, const void *data, size_t size);

bool cxl_memsim_browser_send_message(const char *device, const void *data,
                                     size_t size);
ssize_t cxl_memsim_browser_recv_message(const char *device, void *data,
                                        size_t max_size);

#endif
