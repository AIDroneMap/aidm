@echo off
rem Starts the AIDM engine of this package with the arguments given here.
rem win32env.bat sets the package's environment, then the package's Python
rem runs run.py in UTF-8 mode. The ._pth file of that Python ignores the
rem PYTHONUTF8 variable, so the mode is set on the command line.

setlocal
call "%~dp0win32env.bat"
python -X utf8 "%~dp0run.py" %*
exit /b %ERRORLEVEL%
