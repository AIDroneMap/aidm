set(_proj_name pdal-python)
set(_SB_BINARY_DIR "${SB_BINARY_DIR}/${_proj_name}")

if (WIN32)
  # The bindings compile against the Python that runs configure.py, which
  # has the headers and the import library the package's embeddable Python
  # lacks, and install into the package's site-packages, next to every
  # other wheel. numpy's headers are those of the package's numpy.
  set(PP_EXTRA_ARGS -DPython3_EXECUTABLE=${PYTHON_EXE_PATH}
                    -DPython3_INCLUDE_DIR=${AIDM_PYTHON_INCLUDE_DIR}
                    -DPython3_LIBRARY=${AIDM_PYTHON_LIBRARY}
                    -DPython3_NumPy_INCLUDE_DIR=${PYTHON_HOME}/Lib/site-packages/numpy/_core/include
                    -DPython3_NumPy_INCLUDE_DIRS=${PYTHON_HOME}/Lib/site-packages/numpy/_core/include)
  set(PP_INSTALL_DIR ${PYTHON_HOME}/Lib/site-packages)
else()
  set(PP_EXTRA_ARGS -DPython3_EXECUTABLE=${PYTHON_EXE_PATH}
                    -DPython3_NumPy_INCLUDE_DIRS=${PYTHON_HOME}/lib/python3.12/dist-packages/numpy/_core/include)
  set(PP_INSTALL_DIR ${SB_INSTALL_DIR}/lib/python3.12/dist-packages)
endif()

ExternalProject_Add(${_proj_name}
  DEPENDS           pdal
  PREFIX            ${_SB_BINARY_DIR}
  TMP_DIR           ${_SB_BINARY_DIR}/tmp
  STAMP_DIR         ${_SB_BINARY_DIR}/stamp
  #--Download step--------------
  DOWNLOAD_DIR      ${SB_DOWNLOAD_DIR}
  GIT_REPOSITORY    https://github.com/pierotofy/pdal-python
  GIT_TAG           400
  #--Update/Patch step----------
  UPDATE_COMMAND    git submodule update --init --recursive
  #--Configure step-------------
  SOURCE_DIR        ${SB_SOURCE_DIR}/${_proj_name}
  CMAKE_ARGS
    -DPDAL_DIR=${SB_INSTALL_DIR}/lib/cmake/PDAL
    -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
    -DCMAKE_INSTALL_PREFIX:PATH=${PP_INSTALL_DIR}
    ${WIN32_CMAKE_ARGS}
    ${PP_EXTRA_ARGS}
  #--Build step-----------------
  BINARY_DIR        ${_SB_BINARY_DIR}
  #--Install step---------------
  INSTALL_DIR       ${SB_INSTALL_DIR}
  INSTALL_COMMAND   ${CMAKE_COMMAND} --build <BINARY_DIR> --config $<CONFIG> --target install
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${SB_SOURCE_DIR}/${_proj_name}/src/pdal/__init__.py ${PP_INSTALL_DIR}/pdal
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${SB_SOURCE_DIR}/${_proj_name}/src/pdal/pipeline.py ${PP_INSTALL_DIR}/pdal
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${SB_SOURCE_DIR}/${_proj_name}/src/pdal/drivers.py ${PP_INSTALL_DIR}/pdal
  #--Output logging-------------
  LOG_DOWNLOAD      OFF
  LOG_CONFIGURE     OFF
  LOG_BUILD         OFF
)
