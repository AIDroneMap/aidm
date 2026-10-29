# Shared by the SuperBuild and the Texturing and opc project hooks.
include_guard(GLOBAL)
include(ExternalProject)

set(AIDM_EIGEN_REPOSITORY https://github.com/AIDroneMap/aidm-eigen-mirror.git)

# mvs-texturing downloads Eigen 3.3.2 from GitLab as an archive. Redirect
# that ExternalProject to the same commit in the mirror, without editing
# third-party sources. Leave its paths and build steps intact.
function(ExternalProject_Add name)
    if(name STREQUAL "ext_eigen")
        cmake_parse_arguments(PARSE_ARGV 1 eigen "" "URL;URL_MD5" "")
        if(NOT eigen_URL STREQUAL "https://gitlab.com/libeigen/eigen/-/archive/3.3.2/eigen-3.3.2.tar.gz"
           OR NOT eigen_URL_MD5 STREQUAL "02edfeec591ae09848223d622700a10b")
            message(FATAL_ERROR "The mvs-texturing Eigen pin changed. Update the mirror and EigenMirror.cmake.")
        endif()
        _ExternalProject_Add(${name} ${eigen_UNPARSED_ARGUMENTS}
            GIT_REPOSITORY ${AIDM_EIGEN_REPOSITORY}
            GIT_TAG 477d1e81928a8514d2cca6a6e1e7e478eecd8634
            UPDATE_COMMAND ""
        )
    elseif(name STREQUAL "lightgbm")
        # OpenPointClass downloads LightGBM, whose Eigen submodule is named
        # "eigen" and lives at external_libs/eigen.
        _ExternalProject_Add(${name} ${ARGN}
            GIT_CONFIG submodule.eigen.url=${AIDM_EIGEN_REPOSITORY}
        )
    else()
        _ExternalProject_Add(${name} ${ARGN})
    endif()
endfunction()
