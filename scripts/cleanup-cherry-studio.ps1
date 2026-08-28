# ============================================================
# CherryStudio 受管版 · 彻底卸载残留清理脚本
# 适用：Cherry Studio 卸载后，清理 sidecar/配置/注册表/计划任务残留
# 用法：右键「以管理员身份运行」本脚本（或双击入口 bat）
# 说明：只清理，不卸载主程序；NSSM 服务卸载器已处理，此处兜底
# 版本：2026-08-28
# ============================================================

param(
    [switch]$Force      # 跳过确认提示（静默模式）
)

$ErrorActionPreference = 'Continue'

# ---------- 安全确认 ----------
if (-not $Force) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host " CherryStudio 受管版残留清理脚本" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "将清理以下内容：" -ForegroundColor Cyan
    Write-Host "  1. CherrySidecar 服务（如果还在）"
    Write-Host "  2. 配置目录 CherryManaged（用户级 + 系统级 systemprofile）"
    Write-Host "  3. 应用数据 Cherry Studio（Roaming + Local）"
    Write-Host "  4. 注册表 HKCU\Software\CherryStudio / Cherry"
    Write-Host "  5. 计划任务 StartCherryStudio / CherryBuild"
    Write-Host "  6. 安装目录 D:\Cherry Studio（如存在）"
    Write-Host ""
    $ans = Read-Host "确认清理？输入 Y 继续，其他退出"
    if ($ans -notin @('Y','y','yes','YES')) {
        Write-Host "已取消。" -ForegroundColor Gray
        exit 0
    }
}

Write-Host "`n[1/7] 结束残留进程..." -ForegroundColor Cyan
$procs = Get-Process -Name 'Cherry Studio','Cherry-Studio','sidecar','nssm' -ErrorAction SilentlyContinue
if ($procs) {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    $names = ($procs | Select-Object -ExpandProperty ProcessName -Unique) -join ', '
    Write-Host "  已结束进程: $names"
} else {
    Write-Host "  无残留进程。"
}

Write-Host "[2/7] 移除 CherrySidecar 服务（如存在）..." -ForegroundColor Cyan
$svc = Get-Service -Name 'CherrySidecar' -ErrorAction SilentlyContinue
if ($svc -or (sc.exe query CherrySidecar 2>$null | Select-String 'SERVICE_NAME')) {
    sc.exe stop CherrySidecar 2>$null | Out-Null
    Start-Sleep -Milliseconds 800
    sc.exe delete CherrySidecar 2>$null | Out-Null
    Write-Host "  已删除服务 CherrySidecar。"
} else {
    Write-Host "  服务不存在，跳过。"
}

Write-Host "[3/7] 删除配置目录 CherryManaged..." -ForegroundColor Cyan
$cfgDirs = @(
    "$env:APPDATA\CherryManaged",
    "$env:LOCALAPPDATA\CherryManaged",
    "C:\Windows\System32\config\systemprofile\AppData\Roaming\CherryManaged"  # LocalSystem 服务残留（重要）
)
foreach ($d in $cfgDirs) {
    if (Test-Path $d) {
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  已删除: $d"
    }
}
# 全用户扫描兜底（其它用户账号的 CherryManaged）
Get-ChildItem "C:\Users" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $p = Join-Path $_.FullName "AppData\Roaming\CherryManaged"
    if (Test-Path $p) {
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  已删除: $p"
    }
}

Write-Host "[4/7] 删除应用数据 Cherry Studio..." -ForegroundColor Cyan
$appDirs = @(
    "$env:APPDATA\Cherry Studio",
    "$env:LOCALAPPDATA\Cherry Studio",
    "$env:APPDATA\CherryStudio",
    "$env:LOCALAPPDATA\CherryStudio"
)
foreach ($d in $appDirs) {
    if (Test-Path $d) {
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  已删除: $d"
    }
}

Write-Host "[5/7] 清理注册表..." -ForegroundColor Cyan
foreach ($k in 'HKCU:\Software\CherryStudio', 'HKCU:\Software\Cherry') {
    if (Test-Path $k) {
        Remove-Item -LiteralPath $k -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  已删除注册表: $k"
    }
}
# 环境变量标记（NSIS 卸载器可能漏）
$envKey = 'HKCU:\Environment'
$cur = (Get-ItemProperty -Path $envKey -ErrorAction SilentlyContinue).CHERRY_MANAGED_BUILD
if ($null -ne $cur) {
    Remove-ItemProperty -Path $envKey -Name 'CHERRY_MANAGED_BUILD' -ErrorAction SilentlyContinue
    Write-Host "  已删除环境变量 CHERRY_MANAGED_BUILD"
}

Write-Host "[6/7] 删除计划任务..." -ForegroundColor Cyan
foreach ($t in 'StartCherryStudio', 'CherryBuild') {
    $exists = schtasks /query /tn "\$t" 2>$null | Select-String 'TaskName'
    if ($exists) {
        schtasks /delete /tn "\$t" /f 2>$null | Out-Null
        Write-Host "  已删除计划任务: \$t"
    } else {
        Write-Host "  计划任务 \$t 不存在，跳过。"
    }
}

Write-Host "[7/7] 删除安装目录残留..." -ForegroundColor Cyan
# 常见安装位置（含 D 盘历史版本），全部存在才删
$instDirCandidates = @(
    "C:\Program Files\Cherry Studio",
    "C:\Program Files (x86)\Cherry Studio",
    "D:\Cherry Studio",
    "$env:LOCALAPPDATA\Programs\CherryStudio",
    "$env:LOCALAPPDATA\Programs\Cherry Studio"
)
foreach ($d in $instDirCandidates) {
    if (Test-Path $d) {
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  已删除安装目录: $d"
    }
}

Write-Host ""
Write-Host "============ 清理完成 ============" -ForegroundColor Green
Write-Host "残留验证（应全部为空/不存在）："
Write-Host "  进程: " -NoNewline
$p = Get-Process -Name 'Cherry Studio','sidecar','nssm' -ErrorAction SilentlyContinue
if ($p) { Write-Host "仍有残留!" -ForegroundColor Red } else { Write-Host "无" -ForegroundColor Green }
Write-Host "  目录 CherryManaged: " -NoNewline
if (Test-Path "$env:APPDATA\CherryManaged") { Write-Host "仍在!" -ForegroundColor Red } else { Write-Host "已清" -ForegroundColor Green }
Write-Host "  目录 systemprofile CherryManaged: " -NoNewline
if (Test-Path "C:\Windows\System32\config\systemprofile\AppData\Roaming\CherryManaged") { Write-Host "仍在!" -ForegroundColor Red } else { Write-Host "已清" -ForegroundColor Green }
Write-Host "  服务 CherrySidecar: " -NoNewline
if (sc.exe query CherrySidecar 2>$null | Select-String 'RUNNING|STOPPED') { Write-Host "仍在!" -ForegroundColor Red } else { Write-Host "已清" -ForegroundColor Green }

Write-Host "`n完成。现在可以重新安装 Cherry Studio 受管版。" -ForegroundColor Yellow
if (-not $Force) { Read-Host "`n按回车退出" | Out-Null }