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

enable_testing()
add_test(NAME qemu_legomem_api COMMAND test_qemu_legomem_api)

install(TARGETS qemu_legomem legomem_server test_qemu_legomem_api
    ARCHIVE DESTINATION lib
    RUNTIME DESTINATION bin
)

install(FILES include/qemu_legomem.h
    DESTINATION include/legomem
)

configure_file(${CMAKE_CURRENT_SOURCE_DIR}/launch_qemu_legomem.sh
    ${CMAKE_CURRENT_BINARY_DIR}/launch_qemu_legomem.sh COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/start_server.sh
    ${CMAKE_CURRENT_BINARY_DIR}/start_server.sh COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/test_concurrent.sh
    ${CMAKE_CURRENT_BINARY_DIR}/test_concurrent.sh COPYONLY)
