cmake_minimum_required(VERSION 3.16)
project(adlx_bridge CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ===========================================================================
# ADLX SDK path configuration
#
# OPTION 1: Set ADLX_SDK_DIR to where you cloned the ADLX SDK from GitHub:
#   https://github.com/GPUOpen-LibrariesAndSDKs/ADLX
#
# OPTION 2: Set it as a CMake variable:
#   cmake -DADLX_SDK_DIR="C:/ADLX" ..
#
# The ADLX library (ADLX.dll) is automatically installed with AMD Adrenalin
# drivers to C:/Windows/System32/ADLX.dll — no manual copy needed.
# ===========================================================================
if(NOT DEFINED ADLX_SDK_DIR)
    # Default: assume SDK is checked out alongside this project
    set(ADLX_SDK_DIR "${CMAKE_SOURCE_DIR}/../../ADLX" CACHE PATH "Path to AMD ADLX SDK root")
endif()

if(NOT EXISTS "${ADLX_SDK_DIR}/SDK/ADLXHelper/Windows/Cpp/ADLXHelper.h")
    message(FATAL_ERROR
        "ADLX SDK not found at: ${ADLX_SDK_DIR}\n"
        "Download the ADLX SDK from: https://github.com/GPUOpen-LibrariesAndSDKs/ADLX\n"
        "Then set -DADLX_SDK_DIR=<path to ADLX repo root>"
    )
endif()

message(STATUS "Using ADLX SDK at: ${ADLX_SDK_DIR}")

# Source files
set(SOURCES
    adlx_bridge.cpp
    # ADLX Helper — provides ADLXHelper class (loads ADLX.dll at runtime)
    "${ADLX_SDK_DIR}/SDK/ADLXHelper/Windows/Cpp/ADLXHelper.cpp"
    # Platform utilities — provides adlx_load_library, adlx_free_library, adlx_get_proc_address
    "${ADLX_SDK_DIR}/SDK/Platform/Windows/WinAPIs.cpp"
)

add_executable(adlx_bridge ${SOURCES})

# Include the SDK root so #include "SDK/..." resolves correctly
target_include_directories(adlx_bridge PRIVATE
    "${ADLX_SDK_DIR}"
)

# Windows-specific: link delayimp for runtime DLL loading
if(WIN32)
    target_link_libraries(adlx_bridge PRIVATE
        delayimp
    )
    # ADLX.dll is loaded at runtime via LoadLibrary in ADLXHelper.cpp
    # No import lib needed — it's a delay-loaded runtime load
    target_compile_definitions(adlx_bridge PRIVATE
        _WINDOWS
        WIN32_LEAN_AND_MEAN
        NOMINMAX
    )
endif()

# Optimization flags for Release builds
if(MSVC)
    target_compile_options(adlx_bridge PRIVATE
        $<$<CONFIG:Release>:/O2 /W3>
        $<$<CONFIG:Debug>:/Od /W4 /Zi>
    )
endif()

# Output goes to /Release or /Debug subfolder (Visual Studio default)
set_target_properties(adlx_bridge PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
)

message(STATUS "Build configured. Output: ${CMAKE_BINARY_DIR}/adlx_bridge.exe")
message(STATUS "")
message(STATUS "Build steps:")
message(STATUS "  1. Open VS2019+: File > Open > CMake > select this CMakeLists.txt")
message(STATUS "  2. Select x64 Release configuration")
message(STATUS "  3. Build > Build All")
message(STATUS "  OR via command line:")
message(STATUS "  cmake -B build -DADLX_SDK_DIR=C:/path/to/ADLX")
message(STATUS "  cmake --build build --config Release")
