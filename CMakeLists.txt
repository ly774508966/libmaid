#
# author: w<w359405949@gmail.com>
# created: 2014-08-27
option(maid_test "Build all of maid's tests." OFF)
option(maid_debug "Build all of maid's tests." OFF)
option(maid_tcmalloc "" OFF)

PROJECT(maid)
CMAKE_MINIMUM_REQUIRED(VERSION 2.8)
include(cmake/internal_utils.cmake)
config_compiler_and_linker()

MESSAGE(STATUS "Project: ${PROJECT_NAME}")
MESSAGE(STATUS "Project Directory: ${PORJECT_SOURCE_DIR}")

file(GLOB_RECURSE header include/maid/*.h src/*.h)
file(GLOB_RECURSE source src/*.cc src/middleware/*.cc)
file(GLOB_RECURSE middleware_header src/middleware/*.h)

message("  -> Prepared: maid")

include_directories(
    ${maid_SOURCE_DIR}/include
    ${maid_SOURCE_DIR}/src
    ${maid_SOURCE_DIR}/deps/gtest/include
)


set(cxx_strict "${cxx_strict} -Wno-shadow")
if (maid_debug)
    set(cxx_strict "${cxx_strict} -fPIC -ggdb3 -DDEBUG=1 -O0")
else()
    set(cxx_strict "${cxx_strict} -O3 -fPIC")
endif()

if (maid_static)
    cxx_library(maid ${cxx_strict} ${source})
else()
    cxx_shared_library(maid ${cxx_strict} ${source})
endif()

target_link_libraries(maid
    uv
    protobuf
    glog
    )

################################
# INSTALL
################################
INSTALL(FILES libmaid.so DESTINATION lib)
INSTALL(DIRECTORY ${PROJECT_SOURCE_DIR}/include/maid DESTINATION include)
INSTALL(FILES ${header} DESTINATION include/maid/)
INSTALL(FILES ${middleware_header} DESTINATION include/maid/middleware/)

################################
# TEST
################################
if (maid_test)
    enable_testing()

    ADD_SUBDIRECTORY(deps/gtest)
    cxx_library(maid_main ${cxx_strict} test/maid_main)
    target_link_libraries(maid_main maid gtest)

    cxx_test(maid_channel_factory_test maid_main)
    cxx_test(maid_channel_test maid_main test/test.pb.cc)
    cxx_test(maid_controller_test maid_main )
    cxx_test(maid_buffer_test maid_main )
    cxx_test(maid_closure_test maid_main test/test.pb.cc)
endif()
