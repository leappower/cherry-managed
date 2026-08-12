# 批次 E · Sidecar PyInstaller 打包（E-3）— 技术栈定案

> JJC-20260812-001（批次E）| 2026-08-12
> 权威定义：`任务分解-v4.0.md` E-3 + **SDD 契约（验收标准 AC1-AC7，老板下发）** + 源码校验
> 前置：批次 A 服务端骨架（59b94f7）+ 批次 B Sidecar 闭环（5f877ee）+ 批次 C SDD 修订（8b13293）+ 批次 D E-2 自建更新通道（1302c57）
> 起草：主 Agent（军师子 Agent 长任务易中断，主 Agent 兜底产出，已对齐 SDD 契约）

---

## 一、目标

将 Sidecar 用 PyInstaller 打包为独立可执行文件，**使员工机无需安装 Python 即可运行**。提供跨平台打包方案（build.spec + build_sidecar.sh + build_windows.yml），本机 Linux 验证打包链路，Windows exe 交由 CI 构建。

## 二、技术栈定案（源码已核查）

**入口**：`sidecar.py run`（S-1 主进程），5 个子命令：
- `probe` / `agents` / `models` / `deploy`（sub.add_parser 循环，sidecar.py:335-339）
- `run`（sp_run = sub.add_parser("run")，sidecar.py:345，常驻主进程）

**关键源码事实（决定打包方案）**：
```python
# sidecar.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent       # 开发期=sidecar/ 上级
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))   # → lib/
cfg_path = Path(__file__).resolve().parent / "config" / "sidecar.json"  # → config/
```
- **onefile 下 `__file__` 指向 `_MEIPASS` 临时解压目录**
- `cfg_path` 用 `parent/config/sidecar.json` → **打包后解析为 `_MEIPASS/config/sidecar.json`** → 只要把 config 作为 **datas 内嵌到 `config/` 子目录**，打包后天然可读（AC2/AC5）
- `sys.path.insert(lib)` → 打包后解析为 `_MEIPASS/lib` → lib 模块需内嵌到 `lib/`（hiddenimports 收集 + datas 兜底）
- **config/sidecar.json 实测含 `device.device_id = "dev-sidecar-001"`**（AC5 依据）

**lib 模块**（需 hiddenimports 收集）：`cherry_client` / `ws_client` / `fork_client`

**第三方依赖**：`websocket-client`（`import websocket`，1.9.0，唯一关键第三方）

**config/sidecar.json paths 陷阱**：`paths.user_data = "./data"` 等为**相对路径**，打包后相对 cwd 解析。本批次不改代码逻辑（范围边界），文档注明：真机部署时 NSSM 工作目录设为 exe 所在目录即可（批次 F）。

---

## 三、交付物（对齐 SDD）

| # | 文件 | 说明 |
|---|------|------|
| 1 | `sidecar/scripts/build.spec` | PyInstaller spec（onefile + config 数据内嵌 + websocket/lib 隐藏导入） |
| 2 | `sidecar/scripts/build_sidecar.sh` | 本机打包脚本（依赖检查 + 打包 + 冒烟验证） |
| 3 | `.github/workflows/build_windows.yml` | Windows runner 构建 sidecar.exe（路径 B，输出 artifact） |
| 4 | `docs/批次E-Sidecar打包方案.md` | 本方案（技术栈定案 + AC1-AC7 + 风险对策） |

### 3.1 `sidecar/scripts/build.spec`（核心）
```python
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
SPECPATH = Path(SPECPATH)          # PyInstaller 内置变量：spec 所在目录

a = Analysis(
    [str(SPECPATH.parent.parent / 'sidecar.py')],   # AC1：入口正确指向 sidecar.py
    pathex=[str(SPECPATH.parent.parent), str(SPECPATH.parent.parent / 'lib')],
    binaries=[],
    datas=[
        (str(SPECPATH.parent.parent / 'config' / 'sidecar.json'), 'config'),  # AC2：内嵌到 _MEIPASS/config/
        (str(SPECPATH.parent.parent / 'lib'), 'lib'),                          # lib 兜底内嵌
    ],
    hiddenimports=['websocket', 'cherry_client', 'ws_client', 'fork_client'],  # AC1：websocket+lib 隐藏导入
    ...
    excludes=['pytest', 'httpx', 'fastapi', 'uvicorn'],  # 服务端依赖不打包进 sidecar
)
...
# onefile：EXE(a, ...) 单文件
```

**SPECPATH 处理**：spec 内用 `SPECPATH`（PyInstaller 内置：spec 文件所在目录）拼**绝对路径**定位 sidecar.py/config/lib（契约 AC2 边界项），不依赖 cwd。

### 3.2 `sidecar/scripts/build_sidecar.sh`（本机）
```bash
#!/usr/bin/env bash
set -euo pipefail
# 1. 依赖检查：python3 + PyInstaller（缺失则提示 pip install pyinstaller websocket-client）
# 2. 打包：pyinstaller --clean --onefile --name sidecar build.spec
# 3. 冒烟验证：
#    - dist/sidecar --help  → 输出 5 子命令（probe/agents/models/deploy/run）  [AC4]
#    - dist/sidecar run --config <内嵌>  → 启动读 device_id=dev-sidecar-001  [AC5]
# 产物：dist/sidecar(.exe on Windows)
```

### 3.3 `.github/workflows/build_windows.yml`（路径 B / CI 兜底 cross-compile）
```yaml
name: build-sidecar-windows
on: [workflow_dispatch, push]
jobs:
  build:
    runs-on: windows-latest          # AC6：windows-latest runner
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install pyinstaller websocket-client
      - run: pyinstaller --clean --onefile --name sidecar sidecar/scripts/build.spec
      - uses: actions/upload-artifact@v4
        with:
          name: sidecar-win
          path: dist/sidecar.exe      # AC6：输出 sidecar.exe artifact
```

---

## 四、边界场景与对策（契约要求，逐条）

| # | 边界场景 | 对策 |
|---|---------|------|
| B1 | lib 子目录模块收集失败 | `hiddenimports=['cherry_client','ws_client','fork_client']` + datas 内嵌 `lib/` 兜底 |
| B2 | config 路径打包后失效 | config/sidecar.json 作为 datas 内嵌到 `config/`，onefile 下 `_MEIPASS/config/sidecar.json` 可读；spec 内用 SPECPATH 绝对路径定位源码 |
| B3 | 交叉编译限制（Linux 打不出 Windows exe） | 本机 Linux 验证打包链路（AC3-AC5），Windows exe 由 `build_windows.yml` CI 构建（路径 B），真机验证归批次 F |
| B4 | websocket-client 隐式导入漏 | hiddenimports=['websocket'] + `--collect-submodules websocket` 兜底 |
| B5 | 服务端依赖误打包进 sidecar | excludes=['pytest','httpx','fastapi','uvicorn','fastapi.*'] |
| B6 | paths 相对路径（./data）失效 | 不改代码（范围边界），文档注明 NSSM 工作目录=exe 目录（批次 F） |

---

## 五、验收标准（对齐 SDD，AC1-AC7）

| # | 验收项 | 通过标准 |
|---|--------|---------|
| AC1 | spec 入口正确 | build.spec 入口指向 `sidecar.py`，含 `websocket` + `cherry_client/ws_client/fork_client` hiddenimports |
| AC2 | config 数据内嵌 | config/sidecar.json 作为 datas 内嵌 `config/`，打包后 exe 可读取（archive_viewer 确认 + run 读 device_id） |
| AC3 | 本机 Linux 打包通过 | `build_sidecar.sh` 跑通 PyInstaller 打包，无缺失模块报错 |
| AC4 | 子命令输出 | 打包后 exe `--help` 输出 5 子命令（probe/agents/models/deploy/run） |
| AC5 | run 常驻 + 读内嵌 config | 打包后 exe `run` 启动成功，读内嵌 config（device=dev-sidecar-001） |
| AC6 | workflow 就位 | `.github/workflows/build_windows.yml` 存在，windows-latest runner，输出 sidecar.exe artifact |
| AC7 | 边界对策 | lib 收集失败用 hiddenimports / config 路径用 SPECPATH / cross-compile 用 CI 兜底，均给出对策（见§四） |

**范围边界（不做）**：不产最终 Windows exe 二进制（Linux 无交叉编译，Windows exe 需 CI 或 Windows 机，验证归批次 F）；不做 NSIS 安装壳（E-4 另立）；不改 sidecar 代码逻辑（仅新增打包脚本/配置，除非打包阻塞）；不涉及 Fork 代码；打包产物 `dist/`、`build/`、`.venv-build/` 不入库（gitignore 排除）。

---

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| 本机无 PyInstaller | build_sidecar.sh 依赖检查提示 `pip install pyinstaller websocket-client` |
| Python 3.14 与 PyInstaller 兼容 | CI 用 3.11（稳定）；本机如装失败用 pyenv 固定 |
| onefile 启动慢/杀软误报 | 本批次 onefile；如遇杀软误报，批次 F 评估 onedir |
| `__file__`/`_MEIPASS` 路径依赖 | 已按源码核查：config/lib 内嵌到对应子目录即解析正确 |

---

## 七、更新清单
- `sidecar/scripts/build.spec`（新增）
- `sidecar/scripts/build_sidecar.sh`（新增）
- `.github/workflows/build_windows.yml`（新增）
- `docs/批次E-Sidecar打包方案.md`（本方案）
- `.gitignore`（追加 `dist/`、`build/`、`.venv-build/`）

---

## 八、验收动作（本批次跑通后回填）

> ✅ **已回填（E-3 执行验收，2026-08-12）**：本机 Linux 全链路跑通，AC1-AC7 全部满足。

| AC | 验收项 | 结果 |
|----|--------|------|
| AC1 | spec 入口正确 + hiddenimports | ✅ 入口 `_SIDECAR_DIR/sidecar.py`（SPECPATH 绝对路径）；hiddenimports 含 `websocket`+`cherry_client/ws_client/fork_client`+collect_submodules(websocket) |
| AC2 | config 数据内嵌 | ✅ archive_viewer 确认 `config/sidecar.json` → `config/`、`lib/*.py` → `lib/` 内嵌进包 |
| AC3 | 本机 Linux 打包通过 | ✅ `build_sidecar.sh` 跑通，产 `dist/sidecar`（12MB），无缺失模块报错 |
| AC4 | 子命令输出 | ✅ 打包后 `--help` 输出 5 子命令 probe/agents/models/deploy/run |
| AC5 | run 常驻 + 读内嵌 config | ✅ `run` 启动打印 `Sidecar 启动 device=dev-sidecar-001`；excludes 生效（本机装有 pytest 仍不入包） |
| AC6 | workflow 就位 | ✅ `.github/workflows/build_windows.yml`（windows-latest + setup-python 3.11 + upload sidecar.exe artifact） |
| AC7 | 边界对策 | ✅ B1 hiddenimports+datas lib 兜底；B2 SPECPATH+config 内嵌；B3 CI 出 Windows exe；B4 collect_submodules；B5 excludes；B6 文档注明 NSSM 工作目录 |

- [x] AC3：本机安装 PyInstaller（6.22.0 @ Python 3.14.4）+ 跑通 build_sidecar.sh
- [x] AC1/AC2/AC4/AC5：spec 正确 + config 内嵌 + --help 5 子命令 + run 读 device_id
- [x] AC6：build_windows.yml 就位
- [x] AC5 回归：sidecar pytest 18 不回归（`.................` 100% 通过）
- [ ] commit push GitHub + NAS 同步（本批次 commit 待推）
- [ ] 看板推进 Done 归档
- [ ] 批次 F：CI 出 Windows exe + 真机 NSSM + 升级验证（需老板提供 Windows 机）
