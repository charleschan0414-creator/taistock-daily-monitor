@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="
if exist "%~dp0python_path.txt" set /p PYTHON_EXE=<"%~dp0python_path.txt"
if defined PYTHON_EXE if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" mobile_server.py %*
  exit /b %errorlevel%
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 mobile_server.py %*
  exit /b %errorlevel%
)
where python >nul 2>nul
if %errorlevel%==0 (
  python mobile_server.py %*
  exit /b %errorlevel%
)
echo [ERROR] Python 3.11 not found. Check python_path.txt or install Python. 1>&2
exit /b 1
