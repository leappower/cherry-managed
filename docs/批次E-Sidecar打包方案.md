# 批次 E · Sidecar PyInstaller 打包（E-3）

> JJC-20260812-001（批次E）| 2026-08-12
> 权威定义：`任务分解-v4.0.md` E-3 + `sdd-企业受管版.md` 部署形态
> 前置：批次 A 服务端骨架（59b94f7）+ 批次 B Sidecar 闭环（5f877ee）+ 批次 C SDD 修订（8b13293）+ 批次 D E-2 自建更新通道（1302c57）
> 起草：主 Agent（军师子 Agent 长任务易中断，主 Agent 兜底产出）

---

## 一、范围界定：批次 E 做 vs 不做

**批次 E = Sidecar PyInstaller 打包（E-3）**：
- 提供 PyInstaller 打包配置（`.spec`）+ 打包脚本 + 数据文件收集规则
- 验证 Sidecar 主入口 `sidecar.py run` 在打包形态下可启动（逻辑/依赖完整）
- 交付打包文档（命令 + 产物 + 校验 + NSSM 集成说明）

**明确归批次 F（本批次不做，留接口）**：
- Windows 真机打包出 `sidecar.exe`（PyInstaller **不能跨平台**，Linux 打不出 Windows exe）
- 真机运行验证（无远程通道，需老板给 Windows 机）
- NSSM 服务注册真机安装

**关键约束**：
- 本机 Linux（Python 3.14.4）**无 PyInstaller**，且 PyInstaller 不跨平台 → 本批次交付「配置 + 脚本 + 验证入口」，真机打包归批次 F
- websocket-client 1.9.0 已装（sidecar 唯一关键第三方依赖）

---

## 二、Sidecar 打包目标与依赖（已核查）

**入口**：`sidecar.py run`（S-1 主进程，组装 SidecarRunner：CherryClient/ForkClient/ManagedRegistry/DispatchExecutor/Collector/ReconcileEngine/SelfHealer/WSClient）

**内部模块**（lib/ 兄弟模块，`sys.path.insert` 引用）：
- `lib/cherry_client.py`、`lib/fork_client.py`、`lib/ws_client.py`
- `collect.py`、`dispatch.py`、`managed_registry.py`、`reconcile.py`、`selfheal.py`

**第三方依赖**：
- `websocket-client`（`import websocket`，1.9.0）—— 唯一关键第三方，需 PyInstaller hidden-import 或自动收集

**数据文件**：
- `config/`（sidecar 配置，可选）
- `list.json`（机器清单，可能运行时生成）
- `templates/`（如适用）

---

## 三、打包方案

### 3.1 PyInstaller .spec（交付 `sidecar/sidecar.spec`）
```python
# sidecar.spec — PyInstaller 配置
a = Analysis(
    ['sidecar.py'],
    pathex=['.', 'lib'],          # lib/ 兄弟模块
    hiddenimports=['websocket'],  # websocket-client 显式收集
    ...
)
```
- **pathex 含 lib/**：确保 lib/ 模块被收集
- **hiddenimports**：websocket-client（有时隐式导入需要）
- **datas**：config/、list.json、templates/ 打包进（可选）

### 3.2 打包命令（Windows，批次 F 真机执行）
```bash
pyinstaller --onefile --name sidecar sidecar.spec
# 或
pyinstaller --onedir --name sidecar sidecar.py \
  --paths lib \
  --hidden-import websocket \
  --add-data "config;config" --add-data "list.json;." \
  --collect-submodules websocket
```
- 推荐 `--onefile`（单 exe 便于分发 + NSSM 注册）
- 产物：`dist/sidecar.exe`

### 3.3 本批次验证（Linux 侧）
- 环境缺 PyInstaller → **本批次不实际打 exe**，改为：
  - **入口自检**：`python3 sidecar.py run --help`（确认 run 命令入口可解析）
  - **模块导入自检**：`python3 -c "from sidecar import ...; "` 确认全部模块可导入（无缺失依赖）
  - 交付 `.spec` + `build_sidecar.bat`（Windows 一键打包脚本）+ 文档

---

## 四、交付物

| 文件 | 说明 |
|------|------|
| `sidecar/sidecar.spec` | PyInstaller 配置（pathex lib/ + hiddenimports websocket + datas） |
| `sidecar/scripts/build_sidecar.bat` | Windows 一键打包脚本（装 pyinstaller → pyinstaller → 产物） |
| `docs/批次E-Sidecar打包方案.md` | 本方案（打包命令 + 产物 + 校验 + NSSM 集成说明） |
| `docs/sdd-企业受管版.md` | 部署形态节标注 Sidecar 打包方式 |

**不做**：Windows exe 实际产物、真机验证、NSSM 真机安装（批次 F）。

---

## 五、验收标准

| # | 验收项 | 通过标准 |
|---|--------|---------|
| AC1 | 入口可解析 | `python3 sidecar.py run --help` 返回 0，显示 run 子命令 |
| AC2 | 模块可导入 | 全部 lib/ 模块 + websocket 导入成功（无 MissingModule） |
| AC3 | .spec 正确 | spec 含 pathex=lib + hiddenimports=websocket + datas |
| AC4 | build_sidecar.bat 存在且语法正确 | 脚本含 pyinstaller 命令 + --paths lib + --hidden-import websocket |
| AC5 | 回归 | sidecar pytest 18 不回归 |

---

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| PyInstaller 不跨平台，Linux 打不出 Windows exe | 本批次交付配置+脚本，真机打包归批次 F |
| websocket-client 隐式导入漏 | hiddenimports 显式 + --collect-submodules websocket |
| lib/ 兄弟模块漏收集 | pathex 含 lib/ |
| 数据文件缺（config/list.json） | --add-data 打包 |
| Python 3.14 与 PyInstaller 兼容性 | 打包在 Windows（通常 Python 3.10+ 稳定）；必要时用 pyenv 固定版本 |

---

## 七、更新清单
- `sidecar/sidecar.spec`（新增）
- `sidecar/scripts/build_sidecar.bat`（新增）
- `docs/批次E-Sidecar打包方案.md`（新增）
- `docs/sdd-企业受管版.md`（部署形态节补充）

---

## 八、验收动作（本批次跑通后回填）
- [ ] AC1-AC4 本机验证
- [ ] AC5 sidecar pytest 18 回归
- [ ] commit push GitHub + NAS 同步
- [ ] 看板推进 Done 归档
- [ ] 批次 F：真机 Windows 打包 + NSSM + 升级验证（需老板提供 Windows 机）
