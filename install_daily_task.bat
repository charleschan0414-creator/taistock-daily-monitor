@echo off
setlocal
set "TASK_NAME=TaiStockScannerDaily"
set "RUNNER=%~dp0run.bat"
set "PYTHON_EXE="
where schtasks >nul 2>nul || (echo [ERROR] 找不到 schtasks.exe & exit /b 1)
for /f "usebackq delims=" %%P in (`py -3.11 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (echo [ERROR] 找不到 Python 3.11 & exit /b 1)
set "CMD=schtasks /Create /TN %TASK_NAME% /TR ^"\^"%RUNNER%\^" --update --notify^" /SC DAILY /ST 08:30 /RL LIMITED /IT /F"
if /I "%~1"=="--dry-run" (
  echo [DRY-RUN] Python: %PYTHON_EXE%
  echo [DRY-RUN] %CMD%
  echo 將建立每天 08:30、僅在使用者登入時執行的工作；不需要儲存密碼。
  exit /b 0
)
> "%~dp0python_path.txt" echo %PYTHON_EXE%
%CMD%
if errorlevel 1 exit /b %errorlevel%
echo 已建立排程 %TASK_NAME%。
