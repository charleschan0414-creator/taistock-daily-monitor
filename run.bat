@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHON_EXE="
set "PYTHON_ARGS="
if exist "%~dp0python_path.txt" set /p PYTHON_EXE=<"%~dp0python_path.txt"
if defined PYTHON_EXE if exist "%PYTHON_EXE%" goto launch
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_EXE=py"
  set "PYTHON_ARGS=-3.11"
  goto launch
)
where python >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_EXE=python"
  goto launch
)
echo [ERROR] 找不到 Python 3.11。請重新執行 install_daily_task.bat。 1>&2
exit /b 1

:launch
if /I "%~1"=="--check-only" (
  "%PYTHON_EXE%" %PYTHON_ARGS% updater.py --check-only
  exit /b !errorlevel!
)
"%PYTHON_EXE%" %PYTHON_ARGS% updater.py
if errorlevel 1 echo [WARN] 自動更新失敗，將繼續啟動目前版本。 1>&2
"%PYTHON_EXE%" %PYTHON_ARGS% app.py %*
exit /b !errorlevel!
