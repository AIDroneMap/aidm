add_executable(aidm-weighted-patch-test "${CMAKE_CURRENT_LIST_DIR}/WeightedPatchTest.cpp")
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_custom_target(aidm-weighted-patch-check ALL
    COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_LIST_DIR}/test_patch.py"
    COMMAND $<TARGET_FILE:aidm-weighted-patch-test>
    DEPENDS aidm-weighted-patch-test
    VERBATIM)
