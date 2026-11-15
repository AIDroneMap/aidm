@echo off
rem Sets the environment of the AIDM engine package for the caller.
rem run.bat calls it, and so does the task API's Windows helper before it
rem starts python. It changes the caller's environment, so it has no setlocal.
rem
rem PATH is built from the package's own folders and the Windows folders
rem only, never from the user's PATH, so that no runtime DLL from another
rem program's folder on the user's PATH is loaded first. The package's
rem python folder comes first: the task API starts "python" by name.

chcp 65001 > nul

for %%I in ("%~dp0.") do set "AIDM_ENGINE_DIR=%%~fI"
set "AIDM_INSTALL_DIR=%AIDM_ENGINE_DIR%\SuperBuild\install"

set "PATH=%AIDM_ENGINE_DIR%\python;%AIDM_INSTALL_DIR%\bin;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"

set "GDAL_DATA=%AIDM_INSTALL_DIR%\share\gdal"
set "PROJ_LIB=%AIDM_INSTALL_DIR%\share\proj"
set "PDAL_DRIVER_PATH=%AIDM_INSTALL_DIR%\bin"

rem The osgeo package reads this on Windows and adds every PATH folder to
rem the folders its DLLs load from.
set "USE_PATH_FOR_GDAL_PYTHON=YES"

rem The package's Python ignores PYTHON variables, because its ._pth file
rem runs it in isolated mode. The file 00-aidm.pth that configure.py writes
rem into its site-packages reads this one and applies it before any module
rem of site-packages loads, so the bytecode cache lives under the user's
rem local application data, never in the package folder.
set "PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\AIDM\pycache"
