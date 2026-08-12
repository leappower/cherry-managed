# install_windows_service.ps1
# ============================================================
# CherryStudio 受管版 Sidecar Windows 服务安装脚本 (S-10)
# 用 NSSM 注册 Windows 服务：开机自启 + 崩溃自动重启。
#
# 前置条件:
#   1. 安装 NSSM: https://nssm.cc/download  (nssm.exe 放入 PATH 或本脚本同目录)
#   2. 已安装 Python 3.10+ 并 pip install -r sidecar/requirements.txt
#   3. 确认 sidecar.py 位于 $SidecarDir 下, config/sidecar.json 已配置
#
# 用法(管理员 PowerShell):
#   .\install_windows_service.ps1 -ServiceName CherrySidecar `
#       -SidecarDir "C:\Program Files\CherrySidecar" `
#       -PythonExe "C:\Python311\python.exe"
# ============================================================
param(
    [string]$ServiceName = "CherrySidecar",
    [string]$SidecarDir = "C:\Program Files\CherrySidecar",
    [string]$PythonExe = "C:\Python311\python.exe"
)

$ErrorActionPreference = "Stop"

# 1. 定位 NSSM
$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssm) {
    $localNssm = Join-Path $PSScriptRoot "nssm.exe"
    if (Test-Path $localNssm) {
        $nssm = $localNssm
    } else {
        throw "未找到 nssm.exe。请安装 NSSM(https://nssm.cc/download) 并加入 PATH,或放到脚本同目录。"
    }
} else {
    $nssm = $nssm.Source
}
Write-Host "[1/3] NSSM: $nssm"

# 2. 校验 sidecar.py 存在
$sidecarPy = Join-Path $SidecarDir "sidecar.py"
if (-not (Test-Path $sidecarPy)) {
    throw "未找到 $sidecarPy,请检查 -SidecarDir"
}
Write-Host "[2/3] Sidecar 脚本: $sidecarPy"

# 3. 注册服务
Write-Host "[3/3] 注册服务 $ServiceName ..."
& $nssm install $ServiceName $PythonExe "$sidecarPy" run
if ($LASTEXITCODE -ne 0) {
    throw "nssm install 失败(exit=$LASTEXITCODE)。若服务已存在,请先执行 nssm remove $ServiceName confirm"
}

# 4. 工作目录 = Sidecar 目录(保证相对路径 data/ 等正确)
& $nssm set $ServiceName AppDirectory $SidecarDir
# 5. 崩溃自动重启
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppRestartDelay 5000
# 6. 启动类型: 自动(开机自启)
& $nssm set $ServiceName Start SERVICE_AUTO_START
# 7. 日志重定向
& $nssm set $ServiceName AppStdout "$SidecarDir\logs\sidecar.out.log"
& $nssm set $ServiceName AppStderr "$SidecarDir\logs\sidecar.err.log"
New-Item -ItemType Directory -Force -Path (Join-Path $SidecarDir "logs") | Out-Null

# 8. 启动服务
& $nssm start $ServiceName

Write-Host ""
Write-Host "✅ 服务 $ServiceName 已安装并启动。"
Write-Host "   查看状态: nssm status $ServiceName"
Write-Host "   卸载:     nssm remove $ServiceName confirm"
Write-Host "   日志:     $SidecarDir\logs\"
