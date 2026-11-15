set(_proj_name obj2tiles)
set(_SB_BINARY_DIR "${SB_BINARY_DIR}/${_proj_name}")

set(OBJ2TILES_VERSION v1.5.0)
set(OBJ2TILES_EXT "")

if (WIN32)
  # Windows builds Obj2Tiles from source with the .NET SDK of the build machine.
  # The pin is the commit of the tag v1.5.0, so a moved tag changes nothing.
  # The packages it restores come from the NuGet registry at the versions its
  # project files pin. Nothing is downloaded from a release page.
  set(OBJ2TILES_COMMIT 042c7a0429f6b38b3cb3bfc176b1764435675e23)
  set(OBJ2TILES_NUGET_SOURCE https://api.nuget.org/v3/index.json)
  # The project targets net10.0, so the SDK must be 10 or newer.
  set(OBJ2TILES_MIN_DOTNET_MAJOR 10)

  # CMake regular expressions have no counted repetition, so the length is
  # checked on its own.
  string(LENGTH "${OBJ2TILES_COMMIT}" _obj2tiles_commit_length)
  if (NOT _obj2tiles_commit_length EQUAL 40 OR NOT OBJ2TILES_COMMIT MATCHES "^[0-9a-f]+$")
    message(FATAL_ERROR "SuperBuild/cmake/External-Obj2Tiles.cmake: OBJ2TILES_COMMIT must be a full commit hash, got '${OBJ2TILES_COMMIT}'.")
  endif()

  find_program(DOTNET_EXECUTABLE dotnet)
  if (NOT DOTNET_EXECUTABLE)
    message(FATAL_ERROR "SuperBuild/cmake/External-Obj2Tiles.cmake: the dotnet command is not on PATH. Obj2Tiles is built from source on Windows and needs the .NET SDK ${OBJ2TILES_MIN_DOTNET_MAJOR} or newer.")
  endif()
  execute_process(
    COMMAND ${DOTNET_EXECUTABLE} --version
    OUTPUT_VARIABLE _dotnet_version
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _dotnet_result)
  if (NOT _dotnet_result EQUAL 0 OR NOT _dotnet_version MATCHES "^([0-9]+)\\.")
    message(FATAL_ERROR "SuperBuild/cmake/External-Obj2Tiles.cmake: '${DOTNET_EXECUTABLE} --version' did not print a version (exit ${_dotnet_result}, output '${_dotnet_version}').")
  endif()
  if (CMAKE_MATCH_1 LESS ${OBJ2TILES_MIN_DOTNET_MAJOR})
    message(FATAL_ERROR "SuperBuild/cmake/External-Obj2Tiles.cmake: the .NET SDK is ${_dotnet_version}. Obj2Tiles ${OBJ2TILES_VERSION} targets net10.0 and needs the SDK ${OBJ2TILES_MIN_DOTNET_MAJOR} or newer.")
  endif()
  message(STATUS "Obj2Tiles ${OBJ2TILES_VERSION} (commit ${OBJ2TILES_COMMIT}) is built with the .NET SDK ${_dotnet_version}")

  set(_obj2tiles_publish_dir ${_SB_BINARY_DIR}/publish)

  ExternalProject_Add(${_proj_name}
    PREFIX            ${_SB_BINARY_DIR}
    TMP_DIR           ${_SB_BINARY_DIR}/tmp
    STAMP_DIR         ${_SB_BINARY_DIR}/stamp
    #--Download step--------------
    DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
    GIT_REPOSITORY    https://github.com/OpenDroneMap/Obj2Tiles
    GIT_TAG           ${OBJ2TILES_COMMIT}
    SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
    UPDATE_COMMAND    ""
    CONFIGURE_COMMAND ""
    BUILD_IN_SOURCE 1
    #--Build step-----------------
    # One self-contained executable for win-x64. Restore, compile and publish
    # run in this one step.
    BUILD_COMMAND     ${CMAKE_COMMAND} -E env
                        DOTNET_CLI_TELEMETRY_OPTOUT=1
                        DOTNET_NOLOGO=1
                        DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
                        ${DOTNET_EXECUTABLE} publish Obj2Tiles/Obj2Tiles.csproj
                        --configuration Release
                        --runtime win-x64
                        --self-contained true
                        --output ${_obj2tiles_publish_dir}
                        -p:RestoreSources=${OBJ2TILES_NUGET_SOURCE}
                        -p:PublishSingleFile=true
                        -p:IncludeNativeLibrariesForSelfExtract=true
                        -p:DebugType=none
                        -p:ContinuousIntegrationBuild=true
    #--Install step---------------
    INSTALL_COMMAND   ${CMAKE_COMMAND} -E copy ${_obj2tiles_publish_dir}/Obj2Tiles.exe ${SB_INSTALL_DIR}/bin/Obj2Tiles.exe
              COMMAND ${CMAKE_COMMAND} -E echo "Obj2Tiles ${OBJ2TILES_VERSION} built from commit ${OBJ2TILES_COMMIT} and installed to ${SB_INSTALL_DIR}/bin/Obj2Tiles.exe"
    #--Output logging-------------
    LOG_DOWNLOAD      OFF
    LOG_CONFIGURE     OFF
    LOG_BUILD         OFF
  )
  return()
endif()

set(OBJ2TILES_ARCH "Linux64")
if(${CMAKE_SYSTEM_PROCESSOR} STREQUAL "aarch64")
    set(OBJ2TILES_ARCH "LinuxArm64")
elseif(APPLE)
    set(OBJ2TILES_ARCH "Osx64")
endif()


ExternalProject_Add(${_proj_name}
  PREFIX            ${_SB_BINARY_DIR}
  TMP_DIR           ${_SB_BINARY_DIR}/tmp
  STAMP_DIR         ${_SB_BINARY_DIR}/stamp
  #--Download step--------------
  DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
  URL           https://github.com/OpenDroneMap/Obj2Tiles/releases/download/${OBJ2TILES_VERSION}/Obj2Tiles-${OBJ2TILES_ARCH}.zip
  SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
  UPDATE_COMMAND    ""
  CONFIGURE_COMMAND ""
  BUILD_IN_SOURCE 1
  BUILD_COMMAND     ""
  INSTALL_COMMAND  ${CMAKE_COMMAND} -E copy ${SB_SOURCE_DIR}/${_proj_name}/Obj2Tiles${OBJ2TILES_EXT} ${SB_INSTALL_DIR}/bin
  #--Output logging-------------
  LOG_DOWNLOAD      OFF
  LOG_CONFIGURE     OFF
  LOG_BUILD         OFF
)
