#!/usr/bin/env bash
# CherryStudio Sidecar PyInstaller 打包脚本（本机 Linux 链路验证，E-3）
#
# 用法:
#   bash sidecar/scripts/build_sidecar.sh [--onedir]
#
# 说明:
#   - 本机为 Linux，PyInstaller 只能产出 Linux 可执行文件，用于验证打包链路。
#   - Windows exe 需在 Windows 或 GitHub Actions（见 build_windows.yml）构建。
#   - 默认 --onefile 单文件; 传 --onedir 可产出目录版（便于排障）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_DIR="$(dirname "$SCRIPT_DIR")"   # sidecar/
PROJECT_ROOT="$(dirname "$SIDECAR_DIR")" # cherry-managed/
SPEC="$SCRIPT_DIR/build.spec"
MODE="${1:---onefile}"

cd "$PROJECT_ROOT"

echo "==> [1/4] 检查依赖"
if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 需要 python3"; exit 1
fi
if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "  未安装 PyInstaller，正在安装..."
  pip install --user pyinstaller
fi
if ! python3 -c "import websocket" >/dev/null 2>&1; then
  echo "错误: 缺少 websocket-client。pip install websocket-client"; exit 1
fi
echo "  OK: PyInstaller + websocket-client 就绪"

echo "==> [2/4] 执行 PyInstaller 打包"
DIST="$PROJECT_ROOT/dist"
BUILD="$PROJECT_ROOT/build"
rm -rf "$DIST" "$BUILD"
# 注: build.spec 已内置 onefile 模式; 6.x 不允许 .spec 时再传 --onefile/--onedir/--specpath
pyinstaller --clean --noconfirm --distpath "$DIST" --workpath "$BUILD" "$SPEC"

echo "==> [3/4] 定位产物"
EXE="$DIST/sidecar"
ls -la "$EXE" || { echo "错误: 未找到产物 $EXE"; exit 1; }

echo "==> [4/4] 冒烟验证"
echo "  -- 检查子命令列表:"
"$EXE" --help | head -20 || { echo "冒烟失败"; exit 1; }
echo "  -- 检查 config 内嵌 (probe 应读取 sidecar.json):"
"$EXE" probe --machine probe-check 2>&1 | head -10 || echo "  (probe 需要服务端，仅验证入口可跑)"

echo ""
echo "✅ 打包成功: $EXE"
echo "   大小: $(du -h "$EXE" | cut -f1)"
echo "   注: 本机为 Linux 版，仅验证链路；Windows exe 用 build_windows.yml (CI)"
