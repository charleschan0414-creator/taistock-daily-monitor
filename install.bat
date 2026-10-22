@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "DAILY_TASK=TaiStockScannerDaily"
set "UPDATE_TASK=TaiStockScannerSoftwareUpdate"
set "PYTHON_EXE="

if not "%~2"=="" (
  echo [ERROR] Unsupported arguments.
  exit /b 2
)
if not "%~1"=="" if /I not "%~1"=="--dry-run" if /I not "%~1"=="--repair" (
  echo [ERROR] Use install.bat, install.bat --repair, or install.bat --dry-run.
  exit /b 2
)

for /f "usebackq delims=" %%P in (`py -3.11 -c "import sys; import tkinter; print(sys.executable)"`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE for /f "usebackq delims=" %%P in (`python -c "import sys; import tkinter; print(sys.executable)"`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo [ERROR] Python 3.11 with tkinter was not found.
  exit /b 1
)
if not exist "%PYTHON_EXE%" (
  echo [ERROR] Resolved Python path does not exist.
  exit /b 1
)

 echo [PLAN] Python: %PYTHON_EXE%
 echo [PLAN] TaiStockScannerDaily: every day at 06:30, run.bat --update --notify, missed run starts when available
 echo [PLAN] TaiStockScannerSoftwareUpdate: every 2 hours, updater.py --check-only, missed run starts when available
 echo [PLAN] Shortcuts: Desktop and Start Menu, Unicode name documented in README
 echo [PLAN] Preserve forever: data, public_data, backups, diagnostics, user_settings, python_path
if /I "%~1"=="--dry-run" (
  echo [DRY-RUN] No files, scheduled tasks, or shortcuts were changed.
  exit /b 0
)
if /I "%~1"=="--repair" echo [REPAIR] Replacing scheduled tasks and shortcuts.

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

>"%ROOT%python_path.txt" (echo(%PYTHON_EXE%)
if errorlevel 1 (
  echo [ERROR] Could not write python_path.txt.
  exit /b 1
)

echo [1/4] Creating TaiStockScannerDaily...
schtasks /Create /TN "%DAILY_TASK%" /TR "\"%ROOT%run.bat\" --update --notify" /SC DAILY /ST 06:30 /RL LIMITED /IT /F
if errorlevel 1 (
  echo [ERROR] Could not create %DAILY_TASK%.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Set-ScheduledTask -TaskName 'TaiStockScannerDaily' -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) | Out-Null"
if errorlevel 1 (
  echo [ERROR] Could not enable StartWhenAvailable for %DAILY_TASK%.
  exit /b 1
)

echo [2/4] Creating TaiStockScannerSoftwareUpdate...
set "TSS_ROOT=%ROOT%"
rem schtasks HOURLY /MO 2 avoids PowerShell versions where the Repetition child is null.
rem /IT maps to Task Scheduler XML InteractiveToken; no password is stored.
schtasks /Create /TN "%UPDATE_TASK%" /TR "\"%PYTHON_EXE%\" \"%ROOT%updater.py\" --check-only" /SC HOURLY /MO 2 /ST 00:00 /RL LIMITED /IT /F
if errorlevel 1 (
  echo [ERROR] Could not create %UPDATE_TASK%.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Set-ScheduledTask -TaskName 'TaiStockScannerSoftwareUpdate' -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) | Out-Null"
if errorlevel 1 (
  echo [ERROR] Could not enable StartWhenAvailable for %UPDATE_TASK%.
  exit /b 1
)

echo [3/4] Creating Desktop shortcut...
echo [4/4] Creating Start Menu shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; $name=(-join ([char[]](0x53F0,0x80A1,0x6BCF,0x65E5,0x89C0,0x5BDF)))+'.lnk'; $folders=@([Environment]::GetFolderPath('Desktop'),(Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')); foreach($folder in $folders){$link=$shell.CreateShortcut((Join-Path $folder $name)); $link.TargetPath=(Join-Path $env:TSS_ROOT 'run.bat'); $link.WorkingDirectory=$env:TSS_ROOT.TrimEnd('\'); $link.Save()}"
if errorlevel 1 (
  echo [ERROR] Could not create shortcuts.
  exit /b 1
)

echo [OK] Install or repair completed. Personal data was not changed.
exit /b 0
