cmake_minimum_required(VERSION 3.25.0)
project(LegoMem VERSION 0.1.0)

option(LEGOMEM_BUILD_QEMU_INTEGRATION "Build the QEMU LegoMem direct integration" ON)

enable_testing()

if ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "GNU")
    add_compile_options (-fdiagnostics-color=always)
elseif ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "Clang")
    add_compile_options (-fcolor-diagnostics)
endif ()
execute_process(COMMAND uname -r OUTPUT_VARIABLE arch OUTPUT_STRIP_TRAILING_WHITESPACE)
set(LINUX_SOURCE /lib/modules/${arch}/build/)

set(CMAKE_CXX_STANDARD 20)

# Always build in SERVER_MODE (bpftime dependencies removed)
add_compile_definitions(SERVER_MODE)

# Use std::format instead of fmt for spdlog (C++20)
add_compile_definitions(SPDLOG_USE_STD_FORMAT)

# Use header-only spdlog to avoid library mismatch
add_compile_definitions(SPDLOG_HEADER_ONLY)

add_subdirectory(microbench)
if (LEGOMEM_BUILD_QEMU_INTEGRATION)
    add_subdirectory(qemu_integration)
endif ()

list(APPEND CMAKE_PREFIX_PATH ${CMAKE_BINARY_DIR})

find_package(cxxopts REQUIRED)
find_package(spdlog REQUIRED)
file(GLOB_RECURSE SOURCE_FILES src/legomem*.cpp src/slugarch_model.cpp src/policy.cpp src/helper.cpp src/incore.cpp src/uncore.cpp src/perf.cpp src/monitor.cpp)

execute_process(COMMAND uname -r OUTPUT_VARIABLE arch OUTPUT_STRIP_TRAILING_WHITESPACE)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -fPIC -pthread")
set(CMAKE_EXE_LINKER_FLAGS  "${CMAKE_EXE_LINKER_FLAGS} -latomic")


# Create a static library of LegoMem core functionality
add_library(legomem STATIC ${SOURCE_FILES})
target_include_directories(legomem PUBLIC include ${cxxopts_INCLUDE_DIR} ${spdlog_INCLUDE_DIR})
target_link_libraries(legomem cxxopts::cxxopts spdlog::spdlog_header_only)

# Build latency calculator
add_executable(legomem_latency src/calculateLatency.cc)
set_target_properties(legomem_latency PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
)

# Server library (shared between server executable and tests)
set(SERVER_LIB_SOURCES src/shared_memory_manager.cc src/shm_communication.cpp src/distributed_server.cpp src/tcp_communication.cpp src/region_decoder.cpp src/coherency_engine.cpp)
add_library(legomem_server_lib STATIC ${SERVER_LIB_SOURCES})
target_include_directories(legomem_server_lib PUBLIC include src ${cxxopts_INCLUDE_DIR} ${spdlog_INCLUDE_DIR})
target_link_libraries(legomem_server_lib legomem cxxopts::cxxopts spdlog::spdlog_header_only rt)

# Build server (uses server library + main)
add_executable(legomem_memory_server src/main_server.cc)
target_include_directories(legomem_memory_server PRIVATE include src ${cxxopts_INCLUDE_DIR} ${spdlog_INCLUDE_DIR})
target_link_libraries(legomem_memory_server legomem_server_lib legomem cxxopts::cxxopts spdlog::spdlog_header_only rt)

# Installation targets
install(TARGETS legomem_memory_server
        RUNTIME DESTINATION bin)

install(FILES include/tcp_communication.h
        DESTINATION include/legomem)

add_executable(test_slugarch_model tests/test_slugarch_model.cpp src/slugarch_model.cpp)
target_include_directories(test_slugarch_model PRIVATE include)
add_test(NAME slugarch_model COMMAND test_slugarch_model)

add_test(NAME no_legacy_memory_names
         COMMAND bash ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_no_legacy_memory_names.sh)

add_test(NAME ci_vm_assets_tag_only
         COMMAND bash ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_ci_vm_assets_tag_only.sh)
