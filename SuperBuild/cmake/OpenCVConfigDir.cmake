# Runs on Windows after the OpenCV install step (External-OpenCV.cmake).
# OpenSfM and odm_orthophoto read OpenCV's config from OPENCV_CONFIG_DIR,
# which SuperBuild/CMakeLists.txt builds from the runtime name it gives
# OpenCV. If OpenCV installed its config somewhere else, stop here, before
# they configure, and say where it should be.

foreach(_var OPENCV_CONFIG_DIR MSVC_VERSION OPENCV_RUNTIME)
    if(NOT DEFINED ${_var} OR "${${_var}}" STREQUAL "")
        message(FATAL_ERROR "OpenCVConfigDir.cmake: ${_var} is not set. External-OpenCV.cmake passes it.")
    endif()
endforeach()

if(NOT EXISTS "${OPENCV_CONFIG_DIR}/OpenCVConfig.cmake")
    message(FATAL_ERROR
        "OpenCV built with MSVC_VERSION ${MSVC_VERSION} has no OpenCVConfig.cmake in "
        "${OPENCV_CONFIG_DIR}, the folder OpenSfM and odm_orthophoto read. "
        "SuperBuild/CMakeLists.txt names the OpenCV runtime ${OPENCV_RUNTIME}. "
        "Check that External-OpenCV.cmake passes OpenCV_ARCH and OpenCV_RUNTIME to OpenCV.")
endif()

message(STATUS "OpenCV config for MSVC_VERSION ${MSVC_VERSION}: ${OPENCV_CONFIG_DIR}/OpenCVConfig.cmake")
