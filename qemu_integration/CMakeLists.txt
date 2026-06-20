cmake_minimum_required(VERSION 3.10)
project(QEMULegoMemIntegration LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 20)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)

add_library(qemu_legomem STATIC
    src/qemu_legomem.c
)

target_include_directories(qemu_legomem PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(qemu_legomem
    pthread
)

add_executable(legomem_server
    src/legomem_server_simple.cpp
)

target_link_libraries(legomem_server
    pthread
)

add_executable(test_qemu_legomem_api
    test_qemu_legomem_api.c
)

target_link_libraries(test_qemu_legomem_api
    qemu_legomem
)

add_executable(legomem_qemu_bench
    legomem_qemu_bench.c
)

target_link_libraries(legomem_qemu_bench
    qemu_legomem
)

enable_testing()
add_test(NAME qemu_legomem_api COMMAND test_qemu_legomem_api)
add_test(NAME qemu_legomem_bench_usage COMMAND legomem_qemu_bench --help)
add_test(NAME qemu_legomem_bulk
         COMMAND bash ${CMAKE_CURRENT_SOURCE_DIR}/test_qemu_legomem_bulk.sh
                 $<TARGET_FILE:legomem_server>
                 $<TARGET_FILE:legomem_qemu_bench>)

install(TARGETS qemu_legomem legomem_server test_qemu_legomem_api legomem_qemu_bench
    ARCHIVE DESTINATION lib
    RUNTIME DESTINATION bin
)

install(FILES include/qemu_legomem.h
    DESTINATION include/legomem
)

install(PROGRAMS
    launch_qemu_legomem.sh
    setup_legomem_numa.sh
    start_server.sh
    test_concurrent.sh
    test_qemu_legomem_bulk.sh
    DESTINATION bin
)

install(FILES legomem-numa-setup.service
    DESTINATION share/legomem
)

configure_file(${CMAKE_CURRENT_SOURCE_DIR}/launch_qemu_legomem.sh
    ${CMAKE_CURRENT_BINARY_DIR}/launch_qemu_legomem.sh COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/setup_legomem_numa.sh
    ${CMAKE_CURRENT_BINARY_DIR}/setup_legomem_numa.sh COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/legomem-numa-setup.service
    ${CMAKE_CURRENT_BINARY_DIR}/legomem-numa-setup.service COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/start_server.sh
    ${CMAKE_CURRENT_BINARY_DIR}/start_server.sh COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/test_concurrent.sh
    ${CMAKE_CURRENT_BINARY_DIR}/test_concurrent.sh COPYONLY)
