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
 echo [PLAN] TaiStockScannerDaily: every day at 08:30, run.bat --update --notify
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
schtasks /Create /TN "%DAILY_TASK%" /TR "\"%ROOT%run.bat\" --update --notify" /SC DAILY /ST 08:30 /RL LIMITED /IT /F
if errorlevel 1 (
  echo [ERROR] Could not create %DAILY_TASK%.
  exit /b 1
)

echo [2/4] Creating TaiStockScannerSoftwareUpdate...
set "TSS_ROOT=%ROOT%"
set "TSS_PYTHON=%PYTHON_EXE%"
rem PowerShell emits an XML repetition interval PT2H with StartWhenAvailable.
rem LogonType Interactive maps to Task Scheduler XML InteractiveToken; no password is stored.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $a=New-ScheduledTaskAction -Execute $env:TSS_PYTHON -Argument 'updater.py --check-only' -WorkingDirectory $env:TSS_ROOT; $t=New-ScheduledTaskTrigger -Daily -At '00:00'; $t.Repetition.Interval='PT2H'; $t.Repetition.Duration='P1D'; $s=New-ScheduledTaskSettingsSet -StartWhenAvailable; $p=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited; Register-ScheduledTask -TaskName 'TaiStockScannerSoftwareUpdate' -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null"
if errorlevel 1 (
  echo [ERROR] Could not create %UPDATE_TASK%.
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
