@echo off
REM ============================================================
REM  CherryStudio Managed Edition - Cleanup Launcher
REM  Double-click to run cleanup-cherry-studio.ps1 as Administrator
REM ============================================================
setlocal

REM Request admin rights (UAC elevation)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0cleanup-cherry-studio.ps1' -Verb RunAs"
    exit /b
)

REM Already admin, run directly
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup-cherry-studio.ps1"