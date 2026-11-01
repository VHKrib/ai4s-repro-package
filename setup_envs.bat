@echo off
REM Windows wrapper around setup_envs.ps1 (same arguments).
REM   setup_envs.bat
REM   setup_envs.bat -Force
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_envs.ps1" %*
set RC=%ERRORLEVEL%
endlocal & exit /b %RC%
