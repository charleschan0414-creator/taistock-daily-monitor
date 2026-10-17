@echo off
setlocal EnableExtensions
set "DAILY_TASK=TaiStockScannerDaily"
set "UPDATE_TASK=TaiStockScannerSoftwareUpdate"

if not "%~2"=="" (
  echo [ERROR] Unsupported arguments.
  exit /b 2
)
if not "%~1"=="" if /I not "%~1"=="--dry-run" (
  echo [ERROR] Use uninstall.bat or uninstall.bat --dry-run.
  exit /b 2
)

echo [PLAN] Remove scheduled task %DAILY_TASK%.
echo [PLAN] Remove scheduled task %UPDATE_TASK%.
echo [PLAN] Remove Desktop and Start Menu shortcuts documented in README.
echo [KEEP] Program directory and all personal data remain.
echo [KEEP] data, public_data, backups, diagnostics, user_settings, python_path
if /I "%~1"=="--dry-run" (
  echo [DRY-RUN] Nothing was changed.
  exit /b 0
)

where schtasks >nul 2>nul
if errorlevel 1 (
  echo [ERROR] schtasks.exe was not found.
  exit /b 1
)
where powershell >nul 2>nul
if errorlevel 1 (
  echo [ERROR] powershell.exe was not found.
  exit /b 1
)

schtasks /Query /TN "%DAILY_TASK%" >nul 2>nul
if not errorlevel 1 (
  schtasks /Delete /TN "%DAILY_TASK%" /F
  if errorlevel 1 (
    echo [ERROR] Could not remove %DAILY_TASK%.
    exit /b 1
  )
) else echo [SKIP] %DAILY_TASK% is not installed.

schtasks /Query /TN "%UPDATE_TASK%" >nul 2>nul
if not errorlevel 1 (
  schtasks /Delete /TN "%UPDATE_TASK%" /F
  if errorlevel 1 (
    echo [ERROR] Could not remove %UPDATE_TASK%.
    exit /b 1
  )
) else echo [SKIP] %UPDATE_TASK% is not installed.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $name=(-join ([char[]](0x53F0,0x80A1,0x6BCF,0x65E5,0x89C0,0x5BDF)))+'.lnk'; $paths=@((Join-Path ([Environment]::GetFolderPath('Desktop')) $name),(Join-Path (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs') $name)); foreach($path in $paths){if(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path -Force}}"
if errorlevel 1 (
  echo [ERROR] Could not remove shortcuts.
  exit /b 1
)

echo [OK] Tasks and shortcuts removed. Program and personal data were preserved.
exit /b 0
