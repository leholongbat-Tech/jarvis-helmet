# Host unit tests for the pure C++ parts of the Jarvis Helmet board code (FW4 and others).
#
# Builds with any C++23 compiler; the documented way is the espressif/idf:v6.1 image
# (CMake 4 + g++ 13), see README.md. Every test_*.cpp in this directory is compiled into
# one Catch2 executable together with the pure board sources listed below.
cmake_minimum_required(VERSION 3.24)
project(jarvis_host_tests LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)

# cJSON: its own CMakeLists.txt needs CMake < 4, so only the sources are fetched
# (SOURCE_SUBDIR points to a directory without a CMakeLists.txt) and built below.
FetchContent_Declare(cjson
    URL https://github.com/DaveGamble/cJSON/archive/refs/tags/v1.7.18.tar.gz
    URL_HASH SHA256=3aa806844a03442c00769b83e99970be70fbef03735ff898f4811dd03b9f5ee5
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SOURCE_SUBDIR do-not-add-subdirectory)
FetchContent_Declare(Catch2
    URL https://github.com/catchorg/Catch2/archive/refs/tags/v3.7.1.tar.gz
    URL_HASH SHA256=c991b247a1a0d7bb9c39aa35faf0fe9e19764213f28ffba3109388e62ee0269c
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(cjson Catch2)

add_library(cjson STATIC ${cjson_SOURCE_DIR}/cJSON.c)
target_include_directories(cjson SYSTEM PUBLIC ${cjson_SOURCE_DIR})

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(BOARD_DIR "${REPO_ROOT}/firmware/main/boards/jarvis-helmet")
set(JARVIS_SEQUENCE_FIXTURES_DIR "${REPO_ROOT}/tools/tests/fixtures/sequences"
    CACHE PATH "Shared sequence fixtures (contract 4.11)")

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    set(JARVIS_WARNINGS -Wall -Wextra)
    set(JARVIS_STRICT_WARNINGS -Wall -Wextra -Wpedantic -Werror)
endif()

# Pure board sources (no ESP-IDF headers). Header-only helpers such as helmet_common.h
# and actuator_math.h are reached through the include directory.
add_library(jarvis_board_pure STATIC
    ${BOARD_DIR}/sequence_validator.cc)
target_include_directories(jarvis_board_pure PUBLIC ${BOARD_DIR})
target_link_libraries(jarvis_board_pure PUBLIC cjson)
target_compile_options(jarvis_board_pure PRIVATE ${JARVIS_STRICT_WARNINGS})

# Small generic core helpers the board relies on; stubs/ stands in for esp_log.h.
set(CORE_DIR "${REPO_ROOT}/firmware/main")
add_library(jarvis_core_pure STATIC
    ${CORE_DIR}/protocols/extra_headers.cc)
target_include_directories(jarvis_core_pure PUBLIC ${CORE_DIR}/protocols
    PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/stubs)
target_compile_options(jarvis_core_pure PRIVATE ${JARVIS_STRICT_WARNINGS})

file(GLOB JARVIS_TEST_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/test_*.cpp)
add_executable(jarvis_host_tests ${JARVIS_TEST_SOURCES})
target_link_libraries(jarvis_host_tests PRIVATE jarvis_board_pure jarvis_core_pure
    Catch2::Catch2WithMain)
target_compile_options(jarvis_host_tests PRIVATE ${JARVIS_WARNINGS})
target_compile_definitions(jarvis_host_tests PRIVATE
    JARVIS_BOARD_DIR="${BOARD_DIR}"
    JARVIS_SEQUENCE_FIXTURES_DIR="${JARVIS_SEQUENCE_FIXTURES_DIR}")

enable_testing()
list(APPEND CMAKE_MODULE_PATH ${catch2_SOURCE_DIR}/extras)
include(Catch)
catch_discover_tests(jarvis_host_tests)
