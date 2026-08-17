@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ===== log everything to file too =====
set "LOGFILE=%~dp0verify_managed_result.txt"
echo ============================================================ > "%LOGFILE%"
echo  CherryStudio managed diagnostic  %date% %time% >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"

set "PASS=0"
set "FAIL=0"

echo [1/7] HKLM\Environment managed registry marker
echo [1/7] HKLM\Environment managed registry marker >> "%LOGFILE%"
reg query "HKLM\Environment" /v CHERRY_MANAGED_BUILD >nul 2>&1
if !errorlevel!==0 (
  reg query "HKLM\Environment" /v CHERRY_MANAGED_BUILD 2>nul | findstr /i "CHERRY_MANAGED_BUILD" >> "%LOGFILE%"
  echo      PASS - registry marker (HKLM)
  echo      PASS - registry marker (HKLM) >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - no HKLM registry marker
  echo      FAIL - no HKLM registry marker >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo [2/7] HKCU\Environment managed registry marker
echo [2/7] HKCU\Environment managed registry marker >> "%LOGFILE%"
reg query "HKCU\Environment" /v CHERRY_MANAGED_BUILD >nul 2>&1
if !errorlevel!==0 (
  reg query "HKCU\Environment" /v CHERRY_MANAGED_BUILD 2>nul | findstr /i "CHERRY_MANAGED_BUILD" >> "%LOGFILE%"
  echo      PASS - registry marker (HKCU)
  echo      PASS - registry marker (HKCU) >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - no HKCU registry marker
  echo      FAIL - no HKCU registry marker >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo [3/7] sidecar.exe location
echo [3/7] sidecar.exe location >> "%LOGFILE%"
set "FOUND_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Cherry*" "%ProgramFiles%\Cherry*" "%ProgramFiles(x86)%\Cherry*") do (
  if exist "%%D\resources\sidecar\sidecar.exe" set "FOUND_EXE=%%D\resources\sidecar\sidecar.exe"
)
if defined FOUND_EXE (
  echo      PASS - sidecar.exe at: !FOUND_EXE!
  echo      PASS - sidecar.exe at: !FOUND_EXE! >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - sidecar.exe not found
  echo      FAIL - sidecar.exe not found >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo [4/7] NSSM service CherrySidecar
echo [4/7] NSSM service CherrySidecar >> "%LOGFILE%"
sc.exe query CherrySidecar >nul 2>&1
if !errorlevel!==0 (
  sc.exe query CherrySidecar 2>nul | findstr /i "STATE" >> "%LOGFILE%"
  echo      PASS - CherrySidecar service exists
  echo      PASS - CherrySidecar service exists >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - CherrySidecar service NOT present
  echo      FAIL - CherrySidecar service NOT present >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo [5/7] LAN listen 0.0.0.0:23333
echo [5/7] LAN listen 0.0.0.0:23333 >> "%LOGFILE%"
netstat -ano 2>nul | findstr "LISTENING" | findstr ":23333 " >nul
if !errorlevel!==0 (
  netstat -ano 2>nul | findstr ":23333 " >> "%LOGFILE%"
  echo      PASS - 23333 listening
  echo      PASS - 23333 listening >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - 23333 not listening
  echo      FAIL - 23333 not listening >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo [6/7] sidecar.exe process
echo [6/7] sidecar.exe process >> "%LOGFILE%"
tasklist 2>nul | findstr /i "sidecar.exe" >nul
if !errorlevel!==0 (
  echo      PASS - sidecar.exe running
  echo      PASS - sidecar.exe running >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - sidecar.exe not running
  echo      FAIL - sidecar.exe not running >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo [7/7] CherryStudio process
echo [7/7] CherryStudio process >> "%LOGFILE%"
tasklist 2>nul | findstr /i "CherryStudio.exe" >nul
if !errorlevel!==0 (
  echo      PASS - CherryStudio.exe running
  echo      PASS - CherryStudio.exe running >> "%LOGFILE%"
  set /a PASS+=1
) else (
  echo      FAIL - CherryStudio.exe not running
  echo      FAIL - CherryStudio.exe not running >> "%LOGFILE%"
  set /a FAIL+=1
)
echo.

echo ============================================================
echo  RESULT: PASS=%PASS%  FAIL=%FAIL%
echo ============================================================
echo ============================================================ >> "%LOGFILE%"
echo  RESULT: PASS=%PASS%  FAIL=%FAIL%GH >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
if "%FAIL%"=="0" (
  echo  ALL PASS - openbox ready achieved
  echo  ALL PASS - openbox ready achieved >> "%LOGFILE%"
) else (
  echo  NOT READY - %FAIL% item(s) below target
  echo  NOT READY - %FAIL% item(s) below target >> "%LOGFILE%"
)
echo.
echo  Results written to: %LOGFILE%
echo.
echo  (window stays open - press any key to close)
pause >nul
