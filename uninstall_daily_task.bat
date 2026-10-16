@echo off
setlocal
set "TASK_NAME=TaiStockScannerDaily"
where schtasks >nul 2>nul || (echo [ERROR] 找不到 schtasks.exe & exit /b 1)
if /I "%~1"=="--dry-run" (
  echo [DRY-RUN] schtasks /Delete /TN %TASK_NAME% /F
  exit /b 0
)
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 exit /b %errorlevel%
echo 已移除排程 %TASK_NAME%。
