# -*- mode: python ; coding: utf-8 -*-
"""
CherryStudio 企业受管版 · Sidecar PyInstaller 打包 spec (E-3)

用法（本机 Linux 验证链路）:
    pyinstaller sidecar/scripts/build.spec

注意：
- PyInstaller 不交叉编译。本机 Linux 只产出 Linux 可执行文件用于链路验证；
  Windows exe 需在 Windows 或 GitHub Actions windows-latest runner 上构建
  （见 .github/workflows/build_windows.yml）。
- config/sidecar.json 作为数据文件内嵌。打包后 __file__ 指向 sys._MEIPASS，
  _load_config() 用 Path(__file__).parent/"config"/"sidecar.json" 可正确定位。
- websocket-client 顶层包名为 websocket，需显式 hiddenimports。
"""

from PyInstaller.utils.hooks import collect_submodules

# spec 文件所在目录 (sidecar/scripts/)，据此推导绝对路径，避免 CWD 影响
from pathlib import Path
_SPEC_DIR = Path(SPECPATH).resolve()
_SIDECAR_DIR = _SPEC_DIR.parent          # sidecar/
_PROJECT_ROOT = _SIDECAR_DIR.parent      # cherry-managed/

# Sidecar 纯 Python 依赖（urllib/json/sqlite/threading 等为标准库，自动收集）
# websocket-client 顶层包名为 websocket，需显式收集
hiddenimports = collect_submodules("websocket")

# lib/ 子目录模块：sidecar.py 通过运行时 sys.path.insert 导入，PyInstaller
# 编译期收集不到，需显式列出
for _m in ("cherry_client", "ws_client", "fork_client"):
    hiddenimports.append(_m)

a = Analysis(
    [str(_SIDECAR_DIR / "sidecar.py")],
    pathex=[str(_SIDECAR_DIR), str(_SIDECAR_DIR / "lib")],
    binaries=[],
    datas=[
        (str(_SIDECAR_DIR / "config" / "sidecar.json"), "config"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 常驻进程需 console 看日志；打包版日志写 logs/ 目录
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
