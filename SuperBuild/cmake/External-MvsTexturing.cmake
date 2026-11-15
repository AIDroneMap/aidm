set(_proj_name mvstexturing)
set(_SB_BINARY_DIR "${SB_BINARY_DIR}/${_proj_name}")

set(_mvstexturing_msvc_args "")
if (MSVC)
  # mvs-texturing compiles mapmap's headers, which do not build on MSVC with
  # /arch:AVX2 as they are. MsvcMapmap.h says why and fills the gaps. It is
  # included first in every C++ compile of this project, and only here.
  # Check it before the build relies on it.
  set(_mvstexturing_msvc_dir "${SB_ROOT_DIR}/mvstexturing")
  try_run(_mvstexturing_msvc_run _mvstexturing_msvc_compile
    SOURCES "${_mvstexturing_msvc_dir}/MaskmoveTest.cpp"
    COMPILE_OUTPUT_VARIABLE _mvstexturing_msvc_compile_output
    RUN_OUTPUT_VARIABLE _mvstexturing_msvc_run_output)
  if (NOT _mvstexturing_msvc_compile OR NOT _mvstexturing_msvc_run EQUAL 0)
    message(FATAL_ERROR "${_mvstexturing_msvc_dir}/MaskmoveTest.cpp failed.\n"
                        "${_mvstexturing_msvc_compile_output}\n${_mvstexturing_msvc_run_output}")
  endif()
  message(STATUS "${_mvstexturing_msvc_run_output}")
  list(APPEND _mvstexturing_msvc_args
    "-DCMAKE_CXX_FLAGS_INIT:STRING=/FI\"${_mvstexturing_msvc_dir}/MsvcMapmap.h\"")
endif()

ExternalProject_Add(${_proj_name}
  DEPENDS           mve
  PREFIX            ${_SB_BINARY_DIR}
  TMP_DIR           ${_SB_BINARY_DIR}/tmp
  STAMP_DIR         ${_SB_BINARY_DIR}/stamp
  #--Download step--------------
  DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}/${_proj_name}
  GIT_REPOSITORY    https://github.com/WebODM/mvs-texturing
  GIT_TAG           a56b5e7f95f0bb1dd1e1eb89ce5da202349c0e01
  #--Update/Patch step----------
  UPDATE_COMMAND    ""
  #--Configure step-------------
  SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
  CMAKE_ARGS
    -DCMAKE_PROJECT_Texturing_INCLUDE:FILEPATH=${SB_ROOT_DIR}/cmake/EigenMirror.cmake
    -DRESEARCH=OFF
    -DCMAKE_BUILD_TYPE:STRING=${CMAKE_BUILD_TYPE}
    -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR}
    ${WIN32_CMAKE_ARGS}
    ${_mvstexturing_msvc_args}
    ${APPLE_CMAKE_ARGS}
  #--Build step-----------------
  BINARY_DIR        ${_SB_BINARY_DIR}
  #--Install step---------------
  INSTALL_DIR       ${SB_INSTALL_DIR}
  #--Output logging-------------
  LOG_DOWNLOAD      OFF
  LOG_CONFIGURE     OFF
  LOG_BUILD         OFF
)
