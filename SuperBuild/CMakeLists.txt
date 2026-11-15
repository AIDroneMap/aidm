cmake_minimum_required(VERSION 3.28)

project(ODX-SuperBuild)

# The Docker images build this directory in a single layer, before the rest
# of the repository is copied. A change to any file here rebuilds that layer,
# so every dependency compiles again. See the comment at the top of the
# Dockerfile.

# Setup SuperBuild root location
set(SB_ROOT_DIR ${CMAKE_CURRENT_SOURCE_DIR})

if (NOT CMAKE_BUILD_TYPE)
    message(STATUS "No build type selected, default to Release")
    set(CMAKE_BUILD_TYPE "Release")
endif()

if (WIN32)
    # The Windows libraries come from the deps release pinned in
    # windows-deps.json. configure.py downloads it, checks its hashes and
    # unpacks the export into vcpkg/ at the root of the repository.
    set(AIDM_WINDOWS_DEPS_PIN "${CMAKE_CURRENT_SOURCE_DIR}/windows-deps.json")
    file(READ "${AIDM_WINDOWS_DEPS_PIN}" _aidm_pin)
    string(JSON AIDM_VCPKG_TRIPLET ERROR_VARIABLE _aidm_pin_error GET "${_aidm_pin}" triplet)
    if (_aidm_pin_error)
        message(FATAL_ERROR "${AIDM_WINDOWS_DEPS_PIN}: no triplet: ${_aidm_pin_error}")
    endif()
    get_filename_component(AIDM_VCPKG_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../vcpkg" ABSOLUTE)
    set(CMAKE_TOOLCHAIN_FILE "${AIDM_VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake" CACHE STRING "" FORCE)
    if (NOT EXISTS "${CMAKE_TOOLCHAIN_FILE}" OR NOT EXISTS "${AIDM_VCPKG_ROOT}/installed/${AIDM_VCPKG_TRIPLET}")
        message(FATAL_ERROR "${AIDM_VCPKG_ROOT} holds no deps export for ${AIDM_VCPKG_TRIPLET}. "
                            "Run python configure.py build, which unpacks the export pinned in ${AIDM_WINDOWS_DEPS_PIN}.")
    endif()
endif()

if (MSVC)
    # ADR 0001 on Windows: every tool compiles for AVX2. configure.py puts
    # /arch:AVX2 in the CL environment variable, which MSVC adds to every
    # compile of every external project, CMake, MSBuild or other. Check here
    # that the compiler this build uses really targets AVX2.
    include(CheckCSourceCompiles)
    include(CheckCXXSourceCompiles)
    set(_aidm_avx2_check "#ifndef __AVX2__\n#error not compiled for AVX2\n#endif\nint main(void) { return 0; }\n")
    unset(AIDM_MSVC_C_AVX2 CACHE)
    unset(AIDM_MSVC_CXX_AVX2 CACHE)
    check_c_source_compiles("${_aidm_avx2_check}" AIDM_MSVC_C_AVX2)
    check_cxx_source_compiles("${_aidm_avx2_check}" AIDM_MSVC_CXX_AVX2)
    if (NOT AIDM_MSVC_C_AVX2 OR NOT AIDM_MSVC_CXX_AVX2)
        message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: MSVC does not compile for AVX2. "
                            "Set the CL environment variable to /arch:AVX2, as python configure.py build does.")
    endif()
endif()

if (APPLE)
    find_program(HOMEBREW_EXECUTABLE brew)
    mark_as_advanced(FORCE HOMEBREW_EXECUTABLE)
    if (HOMEBREW_EXECUTABLE)
        # Detected a Homebrew install, query for its install prefix.
        execute_process(COMMAND ${HOMEBREW_EXECUTABLE} --prefix
            OUTPUT_VARIABLE HOMEBREW_INSTALL_PREFIX
            OUTPUT_STRIP_TRAILING_WHITESPACE)
        message(STATUS "Detected Homebrew with install prefix: "
            "${HOMEBREW_INSTALL_PREFIX}, adding to CMake search paths.")
        list(APPEND CMAKE_PREFIX_PATH "${HOMEBREW_INSTALL_PREFIX}")
    endif()

    # Use homebrew's clang compiler since Apple
    # does not allow us to link to libomp
    set(CXX_PATH ${HOMEBREW_INSTALL_PREFIX}/bin/c++-12)
    set(APPLE_CMAKE_ARGS "")
    
    message("Checking for ${CXX_PATH}...")
    if(EXISTS "${CXX_PATH}")
        message("Found Homebrew's C++ compiler: ${CXX_PATH}")
        set(CMAKE_CXX_COMPILER ${CXX_PATH})
        list(APPEND APPLE_CMAKE_ARGS "-DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}")
    endif()

    set(C_PATH ${HOMEBREW_INSTALL_PREFIX}/bin/gcc-12)
    message("Checking for ${C_PATH}...")
    if(EXISTS "${C_PATH}")
        message("Found Homebrew's C compiler: ${C_PATH}")
        set(CMAKE_C_COMPILER ${C_PATH})
        list(APPEND APPLE_CMAKE_ARGS "-DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}")
    endif()

    if (NOT APPLE_CMAKE_ARGS)
        message(WARNING "Cannot find Homebrew's compiler, compilation might fail...")
    endif()
endif()

################################
# Setup SuperBuild directories #
################################

# Setup location where source tar-balls are downloaded
set(SB_DOWNLOAD_DIR "${SB_ROOT_DIR}/download"
    CACHE PATH "Location where source tar-balls are (to be) downloaded.")
mark_as_advanced(SB_DOWNLOAD_DIR)

message(STATUS "SuperBuild files will be downloaded to: ${SB_DOWNLOAD_DIR}")


# Setup location where source tar-balls are located
set(SB_SOURCE_DIR "${SB_ROOT_DIR}/src"
    CACHE PATH "Location where source tar-balls are (will be).")
mark_as_advanced(SB_SOURCE_DIR)
set(SB_BUILD_DIR "${SB_ROOT_DIR}/build")

message(STATUS "SuperBuild source files will be extracted to: ${SB_SOURCE_DIR}")


# Setup location where source tar-balls are located
set(SB_INSTALL_DIR "${SB_ROOT_DIR}/install"
    CACHE PATH "Location where source tar-balls are (will be) installed.")
mark_as_advanced(SB_SOURCE_DIR)

message(STATUS "SuperBuild source files will be installed to: ${SB_INSTALL_DIR}")


# Setup location where binary files are located
set(SB_BINARY_DIR "${SB_ROOT_DIR}/build"
    CACHE PATH "Location where files are (will be) located.")
mark_as_advanced(SB_BINARY_DIR)

message(STATUS "SuperBuild binary files will be located to: ${SB_BINARY_DIR}")

if (WIN32)
  if (NOT DEFINED CMAKE_TOOLCHAIN_FILE)
    message(FATAL_ERROR "CMAKE_TOOLCHAIN_FILE not set. You need to set it to the path of vcpkg.cmake")
  endif()
  set(VCPKG_ROOT "${AIDM_VCPKG_ROOT}")
  set(WIN32_CMAKE_ARGS -DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}
                       -DVCPKG_TARGET_TRIPLET=${AIDM_VCPKG_TRIPLET})
  # The package's Python: configure.py unpacks the embeddable package of the
  # deps release into python/ and installs the wheels of requirements.txt
  # into its Lib/site-packages. That package has no headers and no import
  # library, so tools compile against the Python that runs configure.py, of
  # the same version, which configure.py names in AIDM_PYTHON_EXECUTABLE.
  set(PYTHON_HOME "${SB_ROOT_DIR}/../python")
  set(PYTHON_EXE_PATH "$ENV{AIDM_PYTHON_EXECUTABLE}")
  if (NOT EXISTS "${PYTHON_HOME}/Lib/site-packages" OR NOT EXISTS "${PYTHON_EXE_PATH}")
    message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: no package Python in ${PYTHON_HOME}, or "
                        "AIDM_PYTHON_EXECUTABLE (${PYTHON_EXE_PATH}) names no Python. "
                        "Run python configure.py build, which sets both.")
  endif()
  file(TO_CMAKE_PATH "${PYTHON_EXE_PATH}" PYTHON_EXE_PATH)
  # The headers and import library of that Python. The deps export also
  # holds a python312.lib, from the Python 3.12.9 its pybind11 port brings,
  # which the vcpkg toolchain finds first. Tools that look for Python's
  # libraries get these two paths instead.
  get_filename_component(_aidm_python_root "${PYTHON_EXE_PATH}" DIRECTORY)
  set(AIDM_PYTHON_INCLUDE_DIR "${_aidm_python_root}/include")
  set(AIDM_PYTHON_LIBRARY "${_aidm_python_root}/libs/python312.lib")
  if (NOT EXISTS "${AIDM_PYTHON_INCLUDE_DIR}/Python.h" OR NOT EXISTS "${AIDM_PYTHON_LIBRARY}")
    message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: ${PYTHON_EXE_PATH} has no ${AIDM_PYTHON_INCLUDE_DIR}/Python.h "
                        "or no ${AIDM_PYTHON_LIBRARY}. configure.py needs a full Python 3.12 install, not an embeddable one.")
  endif()

  # On Windows OpenCV installs its config in <arch>/<runtime>/lib and names
  # the runtime from MSVC_VERSION. OpenCV 4.12 has no name for MSVC 14.5x
  # (MSVC_VERSION 195x, Visual Studio 2026), so it would install the config
  # in lib and its consumers would not find it. The build sets both names
  # here. The OpenCV recipe passes them to OpenCV, and every tool that uses
  # OpenCV reads its config from AIDM_OPENCV_DIR.
  set(AIDM_OPENCV_ARCH x64)
  set(AIDM_OPENCV_RUNTIME vc17)
  set(AIDM_OPENCV_DIR "${SB_INSTALL_DIR}/${AIDM_OPENCV_ARCH}/${AIDM_OPENCV_RUNTIME}/lib")
  set(WIN32_OPENCV_ARGS -DOpenCV_DIR=${AIDM_OPENCV_DIR})

  # GDAL, PROJ and PDAL's other libraries come from the deps export. The
  # tools compile against GDAL's headers and import library there: PDAL
  # reads gdal_version.h when it configures, dem2mesh, dem2points,
  # fastrasterfilter and odm_orthophoto include gdal_priv.h. The GDAL
  # Python bindings are the deps release's wheel, which configure.py
  # installs into the package's Python; that wheel has no headers.
  set(AIDM_VCPKG_INSTALLED "${VCPKG_ROOT}/installed/${AIDM_VCPKG_TRIPLET}")
  set(GDAL_INCLUDE_DIR "${AIDM_VCPKG_INSTALLED}/include")
  set(GDAL_LIBRARY "${AIDM_VCPKG_INSTALLED}/lib/gdal.lib")
  foreach(_aidm_gdal_file "${GDAL_INCLUDE_DIR}/gdal_version.h" "${GDAL_INCLUDE_DIR}/gdal_priv.h" "${GDAL_LIBRARY}")
    if (NOT EXISTS "${_aidm_gdal_file}")
      message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: the deps export has no ${_aidm_gdal_file}. "
                          "The tools that use GDAL compile against the export's headers and import library.")
    endif()
  endforeach()

  message("Copying VCPKG DLLs...")
  file(GLOB COPY_DLLS "${AIDM_VCPKG_INSTALLED}/bin/*.dll")
  file(COPY ${COPY_DLLS} DESTINATION "${SB_INSTALL_DIR}/bin")

  # GDAL and PROJ read their data folders at run time: the package points
  # GDAL_DATA and PROJ_LIB at these copies in the install folder. The CMake
  # package files of the export stay out, so no tool of the SuperBuild finds
  # a GDAL or PROJ config in the install folder that points at headers and
  # libraries that are not there.
  foreach(_aidm_data gdal proj)
    if (NOT IS_DIRECTORY "${AIDM_VCPKG_INSTALLED}/share/${_aidm_data}")
      message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: the deps export has no ${AIDM_VCPKG_INSTALLED}/share/${_aidm_data}.")
    endif()
    message("Copying the ${_aidm_data} data folder of the export to ${SB_INSTALL_DIR}/share/${_aidm_data}")
    file(COPY "${AIDM_VCPKG_INSTALLED}/share/${_aidm_data}" DESTINATION "${SB_INSTALL_DIR}/share"
         PATTERN "*.cmake" EXCLUDE)
  endforeach()
  if (NOT EXISTS "${SB_INSTALL_DIR}/share/proj/proj.db")
    message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: ${SB_INSTALL_DIR}/share/proj has no proj.db after the copy.")
  endif()

  # The two Windows variants of ADR 0006. python configure.py build names
  # one. The cpu variant builds OpenMVS and OpenSfM without CUDA. The cuda
  # variant builds both with the CUDA toolkit in AIDM_CUDA_TOOLKIT, through
  # the Visual Studio toolset cuda=<that folder>, which configure.py gives
  # this build with -T. Every external project with the default configure
  # step takes the toolset from this build; the OpenSfM recipe passes it on
  # itself. configure.py copies the CUDA runtime DLLs the tools import into
  # the install folder after the build and checks both variants. No CUDA DLL
  # is copied here.
  set(AIDM_WINDOWS_VARIANT "" CACHE STRING "Windows variant, cpu or cuda. python configure.py build sets it.")
  if (NOT AIDM_WINDOWS_VARIANT STREQUAL "cpu" AND NOT AIDM_WINDOWS_VARIANT STREQUAL "cuda")
    message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: AIDM_WINDOWS_VARIANT is '${AIDM_WINDOWS_VARIANT}', not cpu or cuda. "
                        "Run python configure.py build cpu or python configure.py build cuda.")
  endif()
  if (AIDM_WINDOWS_VARIANT STREQUAL "cuda")
    file(TO_CMAKE_PATH "${AIDM_CUDA_TOOLKIT}" AIDM_CUDA_TOOLKIT)
    if (NOT EXISTS "${AIDM_CUDA_TOOLKIT}/bin/nvcc.exe")
      message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: the cuda variant needs AIDM_CUDA_TOOLKIT, the folder of the "
                          "CUDA toolkit, and '${AIDM_CUDA_TOOLKIT}' has no bin/nvcc.exe. python configure.py build cuda sets it from CUDA_PATH.")
    endif()
    if (NOT CMAKE_GENERATOR_TOOLSET MATCHES "(^|,)cuda=")
      message(FATAL_ERROR "${CMAKE_CURRENT_LIST_FILE}: the cuda variant needs the toolset cuda=${AIDM_CUDA_TOOLKIT}, "
                          "and this build has '${CMAKE_GENERATOR_TOOLSET}'. Run python configure.py build cuda in a new build folder.")
    endif()
    set(AIDM_CUDA_ARGS -DCMAKE_REQUIRE_FIND_PACKAGE_CUDA=ON -DCUDA_TOOLKIT_ROOT_DIR=${AIDM_CUDA_TOOLKIT})
  endif()
  message(STATUS "Windows variant: ${AIDM_WINDOWS_VARIANT}")

  set(WIN32_GDAL_ARGS -DGDAL_FOUND=TRUE -DGDAL_LIBRARY=${GDAL_LIBRARY} -DGDAL_INCLUDE_DIR=${GDAL_INCLUDE_DIR})
elseif(APPLE)
    set(PYTHON_HOME "${SB_ROOT_DIR}/../venv")
    set(PYTHON_EXE_PATH "${PYTHON_HOME}/bin/python")
else()
    set(PYTHON_EXE_PATH "/usr/bin/python3")
    set(PYTHON_HOME "/usr/local/")
endif()

# Path to additional CMake modules
set(CMAKE_MODULE_PATH ${SB_ROOT_DIR}/cmake)

include(ExternalProject)
include(EigenMirror)
include(ExternalProject-Setup)

#########################################
# Download and install third party libs #
#########################################

set(ODM_OpenCV_Version 4.12.0)
option(ODM_BUILD_OpenCV "Force to build OpenCV library" OFF)
SETUP_EXTERNAL_PROJECT(OpenCV ${ODM_OpenCV_Version} ${ODM_BUILD_OpenCV})

set(ODM_GFlags_Version 2.2.2)
option(ODM_BUILD_GFlags "Force to build GFlags library" OFF)
SETUP_EXTERNAL_PROJECT(GFlags ${ODM_GFlags_Version} ${ODM_BUILD_GFlags})

set(ODM_Ceres_Version 2.2.0)
option(ODM_BUILD_Ceres "Force to build Ceres library" OFF)
SETUP_EXTERNAL_PROJECT(Ceres ${ODM_Ceres_Version} ${ODM_BUILD_Ceres})

set(custom_libs Abseil
                LASzip
                OpenSfM
				PDAL
				PDALPython
				Untwine
				Entwine
				MvsTexturing
				OpenMVS
				FPCFilter
				Obj2Tiles
				OpenPointClass
				ExifTool
				RenderDEM
)

externalproject_add(mve
    GIT_REPOSITORY  https://github.com/WebODM/mve.git
    GIT_TAG         066ff646837ef3f95905af45406391f76d4b266d
    UPDATE_COMMAND  ""
    SOURCE_DIR      ${SB_SOURCE_DIR}/mve
    CMAKE_ARGS      ${WIN32_CMAKE_ARGS} ${APPLE_CMAKE_ARGS}
    BUILD_IN_SOURCE 1
    INSTALL_COMMAND ""
)

foreach(lib ${custom_libs})
	SETUP_EXTERNAL_PROJECT_CUSTOM(${lib})
endforeach()

include(ProcessorCount)
ProcessorCount(nproc)

if (WIN32)
    set (POISSON_BUILD_CMD ${CMAKE_MAKE_PROGRAM} ${SB_SOURCE_DIR}/PoissonRecon/PoissonRecon.vcxproj /p:configuration=${CMAKE_BUILD_TYPE} /p:Platform=x64 /p:PlatformToolset=${CMAKE_VS_PLATFORM_TOOLSET} /p:WindowsTargetPlatformVersion=${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION})
    set (POISSON_BIN_PATH "x64/${CMAKE_BUILD_TYPE}/PoissonRecon.exe")
else()
    # SANITIZED_PR makes the flags PoissonRecon's threads share std::atomic.
    # Without it they are plain chars written next to plain data, which
    # works on x86-64 only because x86-64 keeps stores in order; on arm64 a
    # thread can see a flag before its data and abort with "Failed to close
    # loop" (#24). The Makefile appends its own flags to this CFLAGS.
    set (POISSON_BUILD_CMD  ${CMAKE_COMMAND} -E env CFLAGS=-DSANITIZED_PR make -j${nproc} poissonrecon)
    set (POISSON_BIN_PATH "Linux/PoissonRecon")
    if (APPLE)
        set(POISSON_BUILD_CMD COMPILER=${CMAKE_CXX_COMPILER} make -j${nproc} -f Makefile.macos poissonrecon)
    endif()
endif()
externalproject_add(poissonrecon
    GIT_REPOSITORY    https://github.com/pierotofy/PoissonRecon.git
    GIT_TAG           3912
    PREFIX            ${SB_BINARY_DIR}/PoissonRecon
    SOURCE_DIR        ${SB_SOURCE_DIR}/PoissonRecon
    UPDATE_COMMAND    ""
    CONFIGURE_COMMAND ""
    BUILD_IN_SOURCE 1
    BUILD_COMMAND    ${POISSON_BUILD_CMD}
    INSTALL_COMMAND  ${CMAKE_COMMAND} -E copy ${SB_SOURCE_DIR}/PoissonRecon/Bin/${POISSON_BIN_PATH} ${SB_INSTALL_DIR}/bin
)

externalproject_add(dem2mesh
    GIT_REPOSITORY    https://github.com/WebODM/dem2mesh.git
    GIT_TAG           623c8536941175c630fabe4f1352ddc314d98120
    PREFIX            ${SB_BINARY_DIR}/dem2mesh
    SOURCE_DIR        ${SB_SOURCE_DIR}/dem2mesh
    CMAKE_ARGS        -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR}
                      ${WIN32_GDAL_ARGS}
                      ${APPLE_CMAKE_ARGS}
)

externalproject_add(dem2points
    GIT_REPOSITORY    https://github.com/WebODM/dem2points.git
    GIT_TAG           4527122615f41044bde0e79850464ed33549ceeb
    PREFIX            ${SB_BINARY_DIR}/dem2points
    SOURCE_DIR        ${SB_SOURCE_DIR}/dem2points
    CMAKE_ARGS        -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR}
                      ${WIN32_GDAL_ARGS}
                      ${APPLE_CMAKE_ARGS}
)

externalproject_add(odm_orthophoto
    DEPENDS           opencv
    GIT_REPOSITORY    https://github.com/WebODM/odm_orthophoto.git
    GIT_TAG           55b66d87cc168b8ab287bf10e81d4da0890f84f1
    PREFIX            ${SB_BINARY_DIR}/odm_orthophoto
    SOURCE_DIR        ${SB_SOURCE_DIR}/odm_orthophoto
    CMAKE_ARGS        -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR}
                      ${WIN32_CMAKE_ARGS} ${WIN32_GDAL_ARGS} ${WIN32_OPENCV_ARGS}
)

externalproject_add(fastrasterfilter
    GIT_REPOSITORY    https://github.com/WebODM/FastRasterFilter.git
    GIT_TAG           2f16676a9b3d05d3b35ab01b6d2380c8f3a42270
    PREFIX            ${SB_BINARY_DIR}/fastrasterfilter
    SOURCE_DIR        ${SB_SOURCE_DIR}/fastrasterfilter
    CMAKE_ARGS        -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR}
                      ${WIN32_CMAKE_ARGS} ${WIN32_GDAL_ARGS}
)

externalproject_add(lastools
    GIT_REPOSITORY    https://github.com/WebODM/LAStools.git
    GIT_TAG           63ef3a4096c7a74173bccfca800b4e655ee00ae4
    PREFIX            ${SB_BINARY_DIR}/lastools
    SOURCE_DIR        ${SB_SOURCE_DIR}/lastools
    CMAKE_ARGS        -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR}
)

externalproject_add(draco
    GIT_REPOSITORY    https://github.com/pierotofy/draco
    GIT_SHALLOW       ON
    # A clone-local insteadOf rule does not reach submodule clones.
    GIT_CONFIG        submodule.third_party/eigen.url=${AIDM_EIGEN_REPOSITORY}
    GIT_TAG           383
    PREFIX            ${SB_BINARY_DIR}/draco
    SOURCE_DIR        ${SB_SOURCE_DIR}/draco
    CMAKE_ARGS        -DDRACO_TRANSCODER_SUPPORTED=ON 
                      -DCMAKE_INSTALL_PREFIX:PATH=${SB_INSTALL_DIR} 
                      ${WIN32_CMAKE_ARGS}
)
