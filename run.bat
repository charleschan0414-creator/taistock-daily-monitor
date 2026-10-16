@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="
if exist "%~dp0python_path.txt" set /p PYTHON_EXE=<"%~dp0python_path.txt"
if defined PYTHON_EXE if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" app.py %*
  exit /b %errorlevel%
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 app.py %*
  exit /b %errorlevel%
)
where python >nul 2>nul
if %errorlevel%==0 (
  python app.py %*
  exit /b %errorlevel%
)
echo [ERROR] 找不到 Python 3.11。請重新執行 install_daily_task.bat。 1>&2
exit /b 1
