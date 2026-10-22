@echo off
setlocal
cd /d "%~dp0"

rem Start the local read-only mobile server in a minimized window.
start "TaiStock Mobile Server" /min "%~dp0start_mobile.bat" --host 127.0.0.1 --port 8765
>nul timeout /t 2 /nobreak

if not exist "%~dp0tools\cloudflared.exe" (
  echo Downloading Cloudflare Tunnel...
  if not exist "%~dp0tools" mkdir "%~dp0tools"
  curl.exe -L --fail --retry 3 -o "%~dp0tools\cloudflared.exe" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
  if errorlevel 1 (
    echo [ERROR] cloudflared download failed. 1>&2
    exit /b 1
  )
)

echo.
echo A public HTTPS address will appear below. Anyone with the URL can open the read-only dashboard.
echo Keep this window and the computer running. A new URL is created after each restart.
echo.
"%~dp0tools\cloudflared.exe" tunnel --url http://127.0.0.1:8765 --no-autoupdate
