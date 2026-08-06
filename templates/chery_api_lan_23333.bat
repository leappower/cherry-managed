@echo off
chcp 65001 >nul
title Cherry Studio API LAN Manager v2.0

:menu
cls
echo ================================================
echo    Cherry Studio API LAN Manager v2.0
echo ================================================
echo.

:: ── 诊断当前状态 ──
set STATUS=OFF
set FW_STATUS=OFF
set IP_ADDR=UNKNOWN

:: 1. 检查 portproxy 是否存在并且目标 IP 正确
netsh interface portproxy show v4tov4 | findstr ":23333" >nul 2>&1
if %errorlevel% equ 0 (
    :: 进一步验证 portproxy 的 connectaddress 是否指向 127.0.0.1
    netsh interface portproxy show v4tov4 | findstr "127.0.0.1:23333" >nul 2>&1
    if %errorlevel% equ 0 (
        set STATUS=ON
    ) else (
        set STATUS=BROKEN
    )
)

:: 2. 检查防火墙规则
netsh advfirewall firewall show rule name="CherryStudio API" >nul 2>&1
if %errorlevel% equ 0 set FW_STATUS=ON

echo   PortProxy:  [%STATUS%]
echo   Firewall:   [%FW_STATUS%]
echo.

if "%STATUS%"=="BROKEN" (
    echo   ⚠️ PortProxy 存在但指向错误！建议重新 Turn ON
    echo.
)

:: 3. 检测本地 IP
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "192.168.137"') do (
    set IP_ADDR=%%i
    goto :ip_done
)
:ip_done
set IP_ADDR=%IP_ADDR: =%
echo   Local IP: %IP_ADDR%
echo.

:: 4. 检测 CherryStudio 是否在监听
netstat -ano | findstr ":23333" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    netstat -ano | findstr ":23333" | findstr "LISTENING" | findstr "127.0.0.1" >nul 2>&1
    if %errorlevel% equ 0 (
        echo   CherryStudio: [RUNNING (127.0.0.1 only)]
    ) else (
        echo   CherryStudio: [RUNNING (0.0.0.0)]
    )
) else (
    echo   CherryStudio: [NOT RUNNING]
    echo   ⚠️ 请先启动 CherryStudio 并开启 API Server
)
echo.

echo  1. Turn ON  (enable LAN access)
echo  2. Turn OFF (disable LAN access)
echo  3. Diagnose (run diagnostics & show LAN URL)
echo  0. Exit
echo.
set /p choice="Enter (0/1/2/3): "

if "%choice%"=="1" goto enable
if "%choice%"=="2" goto disable
if "%choice%"=="3" goto diagnose
if "%choice%"=="0" goto end
goto menu

:enable
cls
echo.
echo [1/4] 清理旧规则...
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=23333 >nul 2>&1

echo [2/4] 设置端口转发（0.0.0.0:23333 → 127.0.0.1:23333）...
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=23333 connectaddress=127.0.0.1 connectport=23333
if %errorlevel% equ 0 (echo   [OK]) else (echo   [FAIL] 需要管理员权限！ & pause & goto menu)

echo [3/4] 验证转发...
netsh interface portproxy show v4tov4 | findstr "127.0.0.1:23333" >nul
if %errorlevel% equ 0 (echo   [OK]) else (echo   [FAIL] & pause & goto menu)

echo [4/4] 设置防火墙...
netsh advfirewall firewall delete rule name="CherryStudio API" >nul 2>&1
netsh advfirewall firewall add rule name="CherryStudio API" dir=in action=allow protocol=TCP localport=23333
if %errorlevel% equ 0 (echo   [OK]) else (echo   [WARN] 防火墙规则可能未生效)

echo.
echo ===== DONE =====
echo.
echo LAN URL: http://%IP_ADDR%:23333
echo Status:  http://%IP_ADDR%:23333/health
echo.
echo [测试方法]
echo   同一局域网的另一台电脑打开浏览器访问：
echo   http://%IP_ADDR%:23333/health
echo   看到 {"status":"ok"} 即成功
echo.
pause
goto menu

:disable
cls
echo.
echo [1/2] 删除端口转发...
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=23333
if %errorlevel% equ 0 (echo   [OK]) else (echo   [WARN])

echo [2/2] 删除防火墙规则...
netsh advfirewall firewall delete rule name="CherryStudio API" >nul 2>&1
echo   [OK]
echo.
echo ===== DISABLED =====
pause
goto menu

:diagnose
cls
echo ===== Diagnosis Report =====
echo.

:: 测试 loopback 是否通
echo [1] CherryStudio localhost...
curl -s --connect-timeout 2 http://localhost:23333/health 2>nul
if %errorlevel% equ 0 (echo   ✅ OK) else (echo   ❌ FAIL — CherryStudio 未运行或 API Server 未开启)

echo.

:: 测试外网是否通
echo [2] External access...
curl -s --connect-timeout 2 http://%IP_ADDR%:23333/health 2>nul
if %errorlevel% equ 0 (echo   ✅ OK — LAN 可访问) else (echo   ❌ FAIL — 端口转发或防火墙未生效)

echo.

:: 检查 portproxy
echo [3] PortProxy table...
netsh interface portproxy show v4tov4

echo.

:: 检查防火墙
echo [4] Firewall rules...
netsh advfirewall firewall show rule name="CherryStudio API" | findstr /i "Rule Name|Enabled|LocalPort|RemoteIP" 2>nul
if %errorlevel% neq 0 echo   (no rule for CherryStudio API)

echo.

:: 检查端口监听
echo [5] Listening ports...
netstat -ano | findstr ":23333" | findstr "LISTENING"
echo.
echo ===== END =====
pause
goto menu

:end
cls
exit
