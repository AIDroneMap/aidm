set(_proj_name openmvs)
set(_SB_BINARY_DIR "${SB_BINARY_DIR}/${_proj_name}")

externalproject_add(vcg
    GIT_REPOSITORY  https://github.com/WebODM/VCG.git
    GIT_TAG         3fe55c6fa48ca111dab31eaa594ecab33913a8f1
    UPDATE_COMMAND  ""
    SOURCE_DIR      ${SB_SOURCE_DIR}/vcg
    CONFIGURE_COMMAND ""
    BUILD_IN_SOURCE 1
    BUILD_COMMAND   ""
    INSTALL_COMMAND ""
)

externalproject_add(eigen34
    GIT_REPOSITORY  ${AIDM_EIGEN_REPOSITORY}
    GIT_TAG         7176ae16238ded7fb5ed30a7f5215825b3abd134
    UPDATE_COMMAND  ""
    SOURCE_DIR      ${SB_SOURCE_DIR}/eigen34
    CONFIGURE_COMMAND ""
    BUILD_IN_SOURCE 1
    BUILD_COMMAND   ""
    INSTALL_COMMAND ""
)

SET(ARM64_CMAKE_ARGS "")

if(${CMAKE_SYSTEM_PROCESSOR} STREQUAL "aarch64" OR ${CMAKE_SYSTEM_PROCESSOR} STREQUAL "arm64")
  SET(ARM64_CMAKE_ARGS -DOpenMVS_USE_SSE=OFF)
endif()

SET(GPU_CMAKE_ARGS "")
if(UNIX)
    if (EXISTS "/usr/local/cuda/lib64/stubs")
        SET(GPU_CMAKE_ARGS -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs)
    endif()
endif()

# On Windows the variant decides CUDA (ADR 0006). The cpu variant builds
# OpenMVS without CUDA, so its tools import no CUDA DLL and need no driver.
# The cuda variant requires the CUDA toolkit: AIDM_CUDA_ARGS makes OpenMVS's
# find_package(CUDA) stop the configure step when it does not find one,
# instead of building without CUDA. Linux keeps finding the toolkit itself.
if(WIN32)
  if(AIDM_WINDOWS_VARIANT STREQUAL "cuda")
    SET(GPU_CMAKE_ARGS -DOpenMVS_USE_CUDA=ON ${AIDM_CUDA_ARGS})
  else()
    SET(GPU_CMAKE_ARGS -DOpenMVS_USE_CUDA=OFF)
  endif()
endif()

# OpenMVS reads OpenCV's config from one folder. On Windows that is the folder
# SuperBuild/CMakeLists.txt names for OpenCV; the Linux folder holds nothing
# there, and CMake would find OpenCV's Windows pack config in the install
# folder and stop with no binaries for this compiler.
if(WIN32)
  if(NOT DEFINED AIDM_OPENCV_DIR OR "${AIDM_OPENCV_DIR}" STREQUAL "")
    message(FATAL_ERROR "External-OpenMVS.cmake: AIDM_OPENCV_DIR is not set. SuperBuild/CMakeLists.txt sets it on Windows.")
  endif()
  set(_openmvs_opencv_dir "${AIDM_OPENCV_DIR}")
else()
  set(_openmvs_opencv_dir "${SB_INSTALL_DIR}/lib/cmake/opencv4")
endif()

# The weighted patch header uses `#pragma omp simd`. MSVC accepts that pragma
# only with /openmp:experimental, and stops with error C7660 under the plain
# /openmp that OpenMVS gets from FindOpenMP. FindOpenMP picks the experimental
# flag from OpenMP_RUNTIME_MSVC (CMake 3.30 and later; the Windows build runs
# the pinned CMake). Linux compilers do not read the variable.
set(_openmvs_openmp_args "")
if(WIN32)
  set(_openmvs_openmp_args -DOpenMP_RUNTIME_MSVC=experimental)
endif()

ExternalProject_Add(${_proj_name}
  DEPENDS           ceres opencv vcg eigen34
  PREFIX            ${_SB_BINARY_DIR}
  TMP_DIR           ${_SB_BINARY_DIR}/tmp
  STAMP_DIR         ${_SB_BINARY_DIR}/stamp
  #--Download step--------------
  DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
  GIT_REPOSITORY    https://github.com/WebODM/openMVS
  GIT_TAG           988e582f94919408beefd88efc782ba97f6d15ff # head of branch 355 on 2026-10-29
  # The patch step edits these files by exact bytes, with LF line endings.
  # Git for Windows checks out CRLF by default, so the clone keeps LF on every host.
  GIT_CONFIG        core.autocrlf=false
  #--Update/Patch step----------
  UPDATE_COMMAND    ""
  PATCH_COMMAND     "${PYTHON_EXE_PATH}" "${SB_ROOT_DIR}/../docker/patch_openmvs.py"
                    "<SOURCE_DIR>/libs/Common/Config.h"
                    COMMAND ${CMAKE_COMMAND} -DOPENMVS_SOURCE_DIR=<SOURCE_DIR>
                    -P ${CMAKE_CURRENT_LIST_DIR}/OpenMVSWeightedPatch.cmake
  #--Configure step-------------
  SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
  CMAKE_ARGS
    -DOpenCV_DIR=${_openmvs_opencv_dir}
    -DVCG_ROOT=${SB_SOURCE_DIR}/vcg
    -DEIGEN3_INCLUDE_DIR=${SB_SOURCE_DIR}/eigen34/
    -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
    -DCMAKE_INSTALL_PREFIX=${SB_INSTALL_DIR}
    -DOpenMVS_ENABLE_TESTS=OFF
    -DOpenMVS_MAX_CUDA_COMPATIBILITY=ON
    ${_openmvs_openmp_args}
    ${GPU_CMAKE_ARGS}
    ${WIN32_CMAKE_ARGS}
    ${ARM64_CMAKE_ARGS}
  #--Build step-----------------
  BINARY_DIR        ${_SB_BINARY_DIR}
  #--Install step---------------
  INSTALL_DIR       ${SB_INSTALL_DIR}
  #--Output logging-------------
  LOG_DOWNLOAD      OFF
  LOG_CONFIGURE     OFF
  LOG_BUILD         OFF
)
