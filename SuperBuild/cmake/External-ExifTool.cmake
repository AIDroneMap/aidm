set(_proj_name exiftool)
set(_SB_BINARY_DIR "${SB_BINARY_DIR}/${_proj_name}")

if (WIN32)
# The ExifTool zip of the deps release pinned in windows-deps.json, checked
# by its pinned SHA-256. Its one top folder holds exiftool(-k).exe and the
# exiftool_files folder the executable loads at run time. The engine calls
# exiftool, so the executable is installed under that name next to it.
string(JSON _aidm_deps_tag GET "${_aidm_pin}" tag)
string(JSON _aidm_exiftool_file GET "${_aidm_pin}" assets exiftool file)
string(JSON _aidm_exiftool_sha256 GET "${_aidm_pin}" assets exiftool sha256)
ExternalProject_Add(${_proj_name}
  PREFIX            ${_SB_BINARY_DIR}
  TMP_DIR           ${_SB_BINARY_DIR}/tmp
  STAMP_DIR         ${_SB_BINARY_DIR}/stamp
  #--Download step--------------
  DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
  URL               https://github.com/AIDroneMap/aidm-windows-deps/releases/download/${_aidm_deps_tag}/${_aidm_exiftool_file}
  URL_HASH          SHA256=${_aidm_exiftool_sha256}
  SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
  UPDATE_COMMAND    ""
  CONFIGURE_COMMAND ""
  BUILD_IN_SOURCE 1
  BUILD_COMMAND     ""
  INSTALL_COMMAND   ${CMAKE_COMMAND} -E copy "${SB_SOURCE_DIR}/${_proj_name}/exiftool(-k).exe" ${SB_INSTALL_DIR}/bin/exiftool.exe
                    COMMAND ${CMAKE_COMMAND} -E copy_directory ${SB_SOURCE_DIR}/${_proj_name}/exiftool_files ${SB_INSTALL_DIR}/bin/exiftool_files
  #--Output logging-------------
  LOG_DOWNLOAD      OFF
  LOG_CONFIGURE     OFF
  LOG_BUILD         OFF
)
else()
externalproject_add(${_proj_name}
    PREFIX            ${_SB_BINARY_DIR}
    TMP_DIR           ${_SB_BINARY_DIR}/tmp
    STAMP_DIR         ${_SB_BINARY_DIR}/stamp
    SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
    #--Download step--------------
    DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
    URL               https://github.com/exiftool/exiftool/archive/refs/tags/12.62.zip
    UPDATE_COMMAND    ""
    CONFIGURE_COMMAND ""
    BUILD_IN_SOURCE 1
    BUILD_COMMAND    perl Makefile.PL PREFIX=${SB_INSTALL_DIR} LIB=${SB_INSTALL_DIR}/bin/lib
    INSTALL_COMMAND  make install && rm -fr ${SB_INSTALL_DIR}/man
)
endif()