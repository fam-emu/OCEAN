/*
 * QEMU LegoMem host memory backend
 *
 * LegoMem is modeled as a memory server.  This backend exposes a normal
 * QEMU RAM MemoryRegion for NUMA placement while carrying the server metadata
 * needed by QEMU-side LegoMem code.
 *
 * This work is licensed under the terms of the GNU GPL, version 2 or later.
 * See the COPYING file in the top-level directory.
 */

#include "qemu/osdep.h"
#include "system/hostmem.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/module.h"
#include "qom/object_interfaces.h"

OBJECT_DECLARE_SIMPLE_TYPE(HostMemoryBackendLegoMem, MEMORY_BACKEND_LEGOMEM)

struct HostMemoryBackendLegoMem {
    HostMemoryBackend parent_obj;

    char *server;
    uint16_t port;
    uint64_t region_id;
};

static bool legomem_backend_memory_alloc(HostMemoryBackend *backend,
                                         Error **errp)
{
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(backend);
    g_autofree char *name = NULL;
    uint32_t ram_flags;

    if (!backend->size) {
        error_setg(errp, "can't create LegoMem backend with size 0");
        return false;
    }

    if (!lm->server || !lm->server[0]) {
        error_setg(errp, "can't create LegoMem backend without server");
        return false;
    }

    if (!lm->port) {
        error_setg(errp, "can't create LegoMem backend with port 0");
        return false;
    }

    name = host_memory_backend_get_name(backend);
    ram_flags = backend->share ? RAM_SHARED : RAM_PRIVATE;
    ram_flags |= backend->reserve ? 0 : RAM_NORESERVE;
    ram_flags |= backend->guest_memfd ? RAM_GUEST_MEMFD : 0;

    return memory_region_init_ram_flags_nomigrate(&backend->mr, OBJECT(backend),
                                                  name, backend->size,
                                                  ram_flags, errp);
}

static char *legomem_get_server(Object *obj, Error **errp)
{
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);

    return g_strdup(lm->server);
}

static void legomem_set_server(Object *obj, const char *value, Error **errp)
{
    HostMemoryBackend *backend = MEMORY_BACKEND(obj);
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);

    if (host_memory_backend_mr_inited(backend)) {
        error_setg(errp, "cannot change property 'server' of %s",
                   object_get_typename(obj));
        return;
    }

    g_free(lm->server);
    lm->server = g_strdup(value);
}

static void legomem_get_port(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);
    uint16_t value = lm->port;

    visit_type_uint16(v, name, &value, errp);
}

static void legomem_set_port(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    HostMemoryBackend *backend = MEMORY_BACKEND(obj);
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);
    uint16_t value;

    if (host_memory_backend_mr_inited(backend)) {
        error_setg(errp, "cannot change property '%s' of %s", name,
                   object_get_typename(obj));
        return;
    }

    if (!visit_type_uint16(v, name, &value, errp)) {
        return;
    }

    lm->port = value;
}

static void legomem_get_region_id(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);
    uint64_t value = lm->region_id;

    visit_type_uint64(v, name, &value, errp);
}

static void legomem_set_region_id(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    HostMemoryBackend *backend = MEMORY_BACKEND(obj);
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);
    uint64_t value;

    if (host_memory_backend_mr_inited(backend)) {
        error_setg(errp, "cannot change property '%s' of %s", name,
                   object_get_typename(obj));
        return;
    }

    if (!visit_type_uint64(v, name, &value, errp)) {
        return;
    }

    lm->region_id = value;
}

static void legomem_backend_instance_init(Object *obj)
{
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);

    lm->server = g_strdup("127.0.0.1");
    lm->port = 9999;
    lm->region_id = 1;
}

static void legomem_backend_finalize(Object *obj)
{
    HostMemoryBackendLegoMem *lm = MEMORY_BACKEND_LEGOMEM(obj);

    g_free(lm->server);
}

static void legomem_backend_class_init(ObjectClass *oc, const void *data)
{
    HostMemoryBackendClass *bc = MEMORY_BACKEND_CLASS(oc);

    bc->alloc = legomem_backend_memory_alloc;

    object_class_property_add_str(oc, "server",
                                  legomem_get_server,
                                  legomem_set_server);
    object_class_property_set_description(oc, "server",
        "LegoMem memory server address");

    object_class_property_add(oc, "port", "uint16",
                              legomem_get_port,
                              legomem_set_port,
                              NULL, NULL);
    object_class_property_set_description(oc, "port",
        "LegoMem memory server port");

    object_class_property_add(oc, "region-id", "uint64",
                              legomem_get_region_id,
                              legomem_set_region_id,
                              NULL, NULL);
    object_class_property_set_description(oc, "region-id",
        "LegoMem region identifier");
}

static const TypeInfo legomem_backend_info = {
    .name = TYPE_MEMORY_BACKEND_LEGOMEM,
    .parent = TYPE_MEMORY_BACKEND,
    .instance_init = legomem_backend_instance_init,
    .instance_finalize = legomem_backend_finalize,
    .class_init = legomem_backend_class_init,
    .instance_size = sizeof(HostMemoryBackendLegoMem),
};

static void register_types(void)
{
    type_register_static(&legomem_backend_info);
}

type_init(register_types);
