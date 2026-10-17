@echo off
rem Backward-compatible entry point; install.bat now owns install and repair.
call "%~dp0install.bat" %*
exit /b %errorlevel%
