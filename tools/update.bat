@echo off
rem Started by the Update button: waits for MassLeveler Escort to close, pulls what changed on GitHub
rem (files changed only on this PC are kept), then opens it again. %1 = git.exe
cd /d "%~dp0.."
set GIT=%~1
if "%GIT%"=="" set GIT=git
echo Waiting for MassLeveler Escort to close...
:wait
tasklist /FI "IMAGENAME eq MassLeveler Escort.exe" 2>nul | find /I "MassLeveler Escort.exe" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
echo Updating from GitHub...
rem --autostash: files changed on this PC (settings.json ...) are put aside and put back on top
"%GIT%" pull --ff-only --autostash
if errorlevel 1 (
    echo.
    echo UPDATE FAILED - nothing was changed. Send this window to Claude.
    pause
    exit /b 1
)
echo Done - opening MassLeveler Escort
start "" "MassLeveler Escort.exe"
timeout /t 3 /nobreak >nul
