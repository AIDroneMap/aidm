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
    endif()

    # Expanding ARGN as a list drops empty arguments, which would turn
    # INSTALL_COMMAND "" back into the default install command. Forward
    # each argument with bracket quoting, including empty and list values.
    set(call "_ExternalProject_Add(")
    set(skip_value FALSE)
    math(EXPR last "${ARGC} - 1")
    foreach(index RANGE 0 ${last})
        set(arg "${ARGV${index}}")
        if(name STREQUAL "ext_eigen" AND index GREATER 0)
            if(skip_value)
                set(skip_value FALSE)
                continue()
            elseif(arg STREQUAL "URL" OR arg STREQUAL "URL_MD5")
                set(skip_value TRUE)
                continue()
            endif()
        endif()
        set(delimiter "=")
        string(FIND "${arg}" "]${delimiter}]" closing)
        while(NOT closing EQUAL -1)
            string(APPEND delimiter "=")
            string(FIND "${arg}" "]${delimiter}]" closing)
        endwhile()
        string(APPEND call " [${delimiter}[${arg}]${delimiter}]")
    endforeach()

    if(name STREQUAL "ext_eigen")
        string(APPEND call "
            GIT_REPOSITORY ${AIDM_EIGEN_REPOSITORY}
            GIT_TAG 477d1e81928a8514d2cca6a6e1e7e478eecd8634
            UPDATE_COMMAND [=[]=]")
    elseif(name STREQUAL "lightgbm")
        # OpenPointClass downloads LightGBM, whose Eigen submodule is named
        # "eigen" and lives at external_libs/eigen.
        string(APPEND call " GIT_CONFIG submodule.eigen.url=${AIDM_EIGEN_REPOSITORY}")
    endif()
    cmake_language(EVAL CODE "${call})")
endfunction()
