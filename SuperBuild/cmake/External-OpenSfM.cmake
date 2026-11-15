set(_proj_name opensfm)
set(_SB_BINARY_DIR "${SB_BINARY_DIR}/${_proj_name}")
include(ProcessorCount)
ProcessorCount(nproc)

set(EXTRA_INCLUDE_DIRS "")
set(_opensfm_gpu_args "")
if(WIN32)
  # The variant decides CUDA (ADR 0006), as for OpenMVS. OpenSfM turns CUDA
  # on whenever it finds a toolkit, so the cpu variant turns it off. The
  # cuda variant requires the toolkit, for GPU feature matching. This
  # configure step is a command of its own, so it does not take the toolset
  # of the SuperBuild: it gets it here.
  if(AIDM_WINDOWS_VARIANT STREQUAL "cuda")
    set(_opensfm_gpu_args -T "${CMAKE_GENERATOR_TOOLSET}" -DOPENSFM_USE_CUDA=ON ${AIDM_CUDA_ARGS})
  else()
    set(_opensfm_gpu_args -DOPENSFM_USE_CUDA=OFF)
  endif()
  set(OpenCV_DIR "${AIDM_OPENCV_DIR}")
  set(BUILD_CMD ${CMAKE_COMMAND} --build "${SB_BUILD_DIR}/opensfm" --config "${CMAKE_BUILD_TYPE}")
  set(LASZIP_LIB "${SB_INSTALL_DIR}/lib/laszip.lib")

else()
  set(BUILD_CMD make "-j${nproc}")
  if (APPLE)
    set(OpenCV_DIR "${SB_INSTALL_DIR}")
    set(EXTRA_INCLUDE_DIRS "${HOMEBREW_INSTALL_PREFIX}/include")
    set(LASZIP_LIB "${SB_INSTALL_DIR}/lib/liblaszip.dylib")

  else()
    set(OpenCV_DIR "${SB_INSTALL_DIR}/lib/cmake/opencv4")
    set(LASZIP_LIB "${SB_INSTALL_DIR}/lib/liblaszip.so")
  endif()
endif()

ExternalProject_Add(${_proj_name}
  DEPENDS           abseil ceres opencv gflags laszip
  PREFIX            ${_SB_BINARY_DIR}
  TMP_DIR           ${_SB_BINARY_DIR}/tmp
  STAMP_DIR         ${_SB_BINARY_DIR}/stamp
  #--Download step--------------
  DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
  GIT_REPOSITORY    https://github.com/WebODM/OpenSfM/
  GIT_TAG           a125a3d16ed0a4df5e9e3d99475ee7f1f9ac24b8
  #--Update/Patch step----------
  UPDATE_COMMAND    git submodule update --init --recursive
  #--Configure step-------------
  SOURCE_DIR        ${SB_INSTALL_DIR}/bin/${_proj_name}
  CONFIGURE_COMMAND ${CMAKE_COMMAND} <SOURCE_DIR>/${_proj_name}/src
    -DCERES_ROOT_DIR=${SB_INSTALL_DIR}
    -DOpenCV_DIR=${OpenCV_DIR}
    -DADDITIONAL_INCLUDE_DIRS=${SB_INSTALL_DIR}/include
    -DYET_ADDITIONAL_INCLUDE_DIRS=${EXTRA_INCLUDE_DIRS}
    -DOPENSFM_BUILD_TESTS=off
    -DPYTHON_EXECUTABLE=${PYTHON_EXE_PATH}
    -DLASZIP_LIBRARY=${LASZIP_LIB}
    -DLASZIP_INCLUDE_DIR=${SB_INSTALL_DIR}/include/laszip
    -Dabsl_DIR=${SB_INSTALL_DIR}/lib/cmake/absl
    -DCMAKE_INSTALL_PREFIX=${SB_INSTALL_DIR}/bin/${_proj_name}/opensfm
    ${WIN32_CMAKE_ARGS}
    ${_opensfm_gpu_args}
  BUILD_COMMAND ${BUILD_CMD}
  #--Build step-----------------
  BINARY_DIR        ${_SB_BINARY_DIR}
  #--Output logging-------------
  LOG_DOWNLOAD      OFF
  LOG_CONFIGURE     OFF
  LOG_BUILD         OFF
)
