# 批次G：M3 Web 管理后台 + E-4 安装包集成+自动配对 方案

> 文档版本：v1.0 | 日期：2026-08-13 | 状态：review
> 项目：cherry-managed | 看板：JJC-20260812-001
> 前置：M2 批次A-E 已完成（server骨架 + Sidecar闭环 + 更新通道E-2 + PyInstaller打包E-3）
> 本方案补齐两处 SDD 缺口：**E-4 安装包集成+自动配对**（SDD 仅 V-M4-1 一句验收，缺详细规范）与 **M3 Web 管理后台**（SDD §1.1/§5/§9 有规范未实现）。

---

## 0. 现状与关键缺口（已核实）

- server 骨架：`server/main.py`（FastAPI+uvicorn, 端口 2334）+ `ws_server.py` + `db.py`（5 表含 `audit_log`）+ `feed.py`（E-2 更新通道）。
- sidecar：`sidecar/sidecar.py`（`run` 子命令常驻）+ `lib/ws_client.py` + `lib/fork_client.py` + `dispatch/collect/reconcile/managed_registry/selfheal`。
- E-3 打包：`sidecar/scripts/build.spec`（onefile）+ `build_sidecar.sh` + `install_windows_service.ps1`（NSSM）+ `.github/workflows/build_windows.yml`。
- **⚠️ 关键缺口**：当前 `device_id/hostname` 硬编码在 `sidecar.json` 且随包内嵌（`_load_config` 读 `_MEIPASS/config/sidecar.json`，只读），**无运行时设备标识自动生成、无 server URL/token 自动配对的落盘机制**。这正是 E-4 要补的。
- **⚠️ 相关缺口**：`server/` 尚无 `web_admin/` 模块（M3 Web 后台未实现）。

---

## Part 1: E-4 安装包集成 + 自动配对

### 1.1 总览

老板拍板：Sidecar 集成进 Fork 安装包（NSIS），员工安装后**首次启动自动注册 Windows 服务 + 自动连服务器配对（零手动配置）**。

落地三段式：
1. **构建期**：GitHub Actions windows-latest 出 `sidecar.exe`（已通 E-3），再在 electron-builder NSIS 里把 `sidecar.exe` + 预置 `config/sidecar.json`（含编译进包的 server URL + token）打进 Fork 安装包。
2. **首次启动期**：Fork 安装后首次启动，由 NSIS 后置脚本 / 首启引导器注册 NSSM 服务 + 生成设备标识 + 落盘用户级可写配置。
3. **配对期**：Sidecar 服务起来 → 读用户级配置 → WS register（device_id + token）→ 服务端设备注册表出现 → 受管标记生效。

### 1.2 NSIS 如何把 sidecar.exe + config 打进 Fork 安装包

- Fork 用 electron-builder（electron-updater），`electron-builder.yml` 的 `nsis.include` 指向自定义 NSIS 脚本 `extra.nsh`。
- 构建产物阶段，把 `sidecar.exe` 复制进 Fork 的 `extraResources`：
  ```
  extraResources:
    - { from: dist/sidecar.exe, to: sidecar/sidecar.exe }
    - { from: sidecar/config/sidecar.json, to: sidecar/config/sidecar.json }
    - { from: sidecar/scripts/nssm.exe, to: sidecar/nssm.exe }
  ```
  electron-builder 会把 `extraResources` 解压到 `process.resourcesPath/sidecar/`。
- NSIS 安装完成事件（`!macro customInstall`）里调用 `ExecWait '"$INSTDIR\resources\sidecar\sidecar.exe" --install-service'`，完成服务注册与首启配对引导。
- 卸载（`customUnInstall`）里调用 `ExecWait '"$INSTDIR\resources\sidecar\sidecar.exe" --uninstall-service'`，再删用户级配置与数据。

> ⚠️ 当前 `build_windows.yml` 只出 sidecar.exe 一个 artifact，**未接入 Fork 的 electron-builder NSIS 装配**。E-4 需在 Fork 侧新增 workflow 步骤，下载 sidecar artifact 后并入 `extraResources`。此链路走 CI，本机 Linux 无法本地验证（无 Wine/Windows 包链），验收对齐 V-M0-2 方式（CI 产物 + 测试机安装，真机归批次 F）。

### 1.3 首次启动自动注册 NSSM 服务

- 复用现有 `install_windows_service.ps1` 的 NSSM 逻辑，但**改由 sidecar 自身的 `--install-service` 子命令驱动**（PowerShell 在 NSIS 中调用需 `-ExecutionPolicy`，易受策略/杀软干扰；改用 exe 子命令更稳）。
- 安装语义（`--install-service`）：
  - 定位 NSSM：优先 `resources/sidecar/nssm.exe`（随包内置），否则报错留日志。
  - `nssm install CherrySidecar <sidecar.exe> run`；`set AppDirectory <用户级数据目录>`；`Start SERVICE_AUTO_START`；`AppExit Default Restart`；`AppRestartDelay 5000`；stdout/stderr 重定向到 `<数据目录>\logs\`。
  - 服务已存在（升级场景）：先 `nssm stop/remove` 重建，保证升级后指向新 exe。
- **幂等**：重复安装不报错，已存在则更新配置后重启。
- 需管理员权限：NSIS 安装本身提权（`RequestExecutionLevel admin`），首启有权限注册服务。**降级兜底**：若首启提权失败（UAC 限制），降级为「用户登录自启」（`Run` 注册表键 + 托盘），写日志，下次 Sidecar 检测到未注册为服务时自动补注册。

### 1.4 自动配对流程

配对 = 设备首次与服务器建立受管关系。四要素：**服务器地址/token（从哪来）、设备标识自动生成、配置落盘、上报配对时序**。

1. **服务器地址/token 从哪来 / 如何编译进包**：
   - 服务器 `url`（`ws://server:2334/ws`）与 `token`（与 `config.json` 的 `token` 一致）在构建时写入打包用 `sidecar.json` 的 `server` 段。
   - 落地：GitHub Actions workflow 从 secrets（`SERVER_URL`、`DEVICE_TOKEN`）读取，CI 生成 `sidecar/config/sidecar.json` 再交给 PyInstaller 内嵌。**token 不写死进源码仓库**，走 CI secret。

2. **设备标识自动生成**：
   - 首启无用户级配置时生成 `device_id` = `managed-` + 机器指纹（`HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` + 主机名 hash），跨重启稳定、同机唯一。
   - `hostname` = `socket.gethostname()`；`os="windows"`；`group` 用内嵌默认或留空待分组。

3. **配置落盘位置（解决 `_MEIPASS` 只读）**：
   - 用户级可写目录：`%PROGRAMDATA%\CherryManaged\`（机器级，服务账户可写）作为配置与数据根：`config.json`（运行时可写）+ `device.json`（设备标识）+ `logs/`。
   - 改造 `_load_config()`：先读用户级 `<PROGRAMDATA>\CherryManaged\config.json`，不存在则用内嵌 `_MEIPASS/config/sidecar.json` 作为模板生成并落盘——兼容现有打包逻辑，支持运行时持久化。

4. **上报配对时序**：
   - 首启 → 生成/读 device_id → 落盘用户级 config → `--install-service` 注册并启动 NSSM → 服务内 `_register()` 发 `register` → 服务端 `devices` 表 `online=1` → 服务端可派发 → Sidecar 对账建立受管标记 → 配对完成。**员工零输入**。

### 1.5 新增 sidecar 子命令清单

| 子命令 | 语义 | 验收词 |
|--------|------|--------|
| `sidecar.exe --install-service` | 注册/更新 NSSM 服务并启动 | 断言服务存在、StartType=Auto、进程在跑 |
| `sidecar.exe --uninstall-service` | 停止并移除 NSSM 服务 | 断言服务不存在 |
| `sidecar.exe --first-run` | 生成/读 device_id、落盘用户级 config、触发安装服务 | 断言 device.json 生成、config.json 落盘 |
| `sidecar.exe run`（已有） | 常驻主进程，读取用户级 config | 断言 register 上报、device 入服务端注册表 |

### 1.6 卸载器语义（卸载 Fork 是否卸载 Sidecar）

遵循 SDD V-M4-4「三种卸载」选定语义：
1. **正常卸载 Fork**：同时 `--uninstall-service` 卸载 Sidecar 服务 + 删 `<PROGRAMDATA>\CherryManaged` 数据 → **彻底卸载**（不留孤儿服务/数据）。
2. **仅停用（不卸载）**：`nssm stop CherrySidecar`，不删数据（临时维护）。
3. **Fork 升级/重装**：不动 Sidecar 服务，仅更新 exe 与 config（服务保活，受管状态不丢，对齐 V-M4-2）。
- 卸载时 `customUnInstall` 先停服务再删文件（Windows 不允许删运行中的 exe）。

### 1.7 与批次 E sidecar.exe 衔接

- 保留现有 `sidecar.py` / `build.spec` / `build_windows.yml` 产出的 `sidecar.exe` 为唯一侧车二进制；E-4 只在其上**新增 4 个子命令 + config 落盘改造 + 接入 NSIS**，不改既有派发/采集/对账/自愈逻辑，避免回归 M2 已验收功能。
- `install_windows_service.ps1` 保留用于手动运维，与 `--install-service` 并行不冲突。

### 1.8 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| **杀软误报**（PyInstaller exe + NSSM + 自启动 + 网络回连） | 🔴 高 | 1) 提交证书签名（EV/OV 代码签名）降误报；2) 文档化白名单指引（加白 sidecar.exe、PROGRAMDATA 目录、NSSM 服务）；3) 首启日志可追踪 |
| **config 落盘位置**（`_MEIPASS` 只读 / 权限） | 🟡 中 | 用户级 `%PROGRAMDATA%\CherryManaged`；首启提权失败降级 Run 自启并日志提示 |
| **多实例 / 同机多用户** | 🟡 中 | device_id 基于机器级 MachineGuid（非用户级），同机唯一；服务唯一名 `CherrySidecar`；安装幂等 |
| **配对失败重试** | 🟡 中 | 复用现有 WS 指数退避重连（initial 1s / max 60s）；首次 register 失败保留 pending 定时重试；失败不阻塞服务常驻 |
| **卸载后孤儿** | 🟡 中 | 正常卸载路径 `--uninstall-service` + 删数据目录；验收 V-M4-4 |
| **NSSM 未随包内置** | 🟡 中 | extraResources 内置 nssm.exe；缺失时报错留日志，不静默失败 |

---

## Part 2: M3 Web 管理后台

### 2.1 模块结构（`server/web_admin/`）

依据 SDD §1.1 `web_admin/` 目录，新建 `server/web_admin/`：
```
server/web_admin/
├── __init__.py        # APIRouter 组装
├── auth.py            # 管理员登录鉴权（账号校验 + session）
├── router.py          # 页面路由 + 登录/登出 + 审计查询
├── audit.py           # 审计日志读写封装（封装 db.audit + 查询）
└── templates/         # Jinja2 模板（login.html, dashboard.html, audit.html）
```
FastAPI 用 `APIRouter` + `Jinja2Templates`，不引入重型前端框架（对齐 §5「简单前端」）。

### 2.2 管理员登录鉴权

- **账号存储**：复用 `db.py` 新增 `admin_user` 表（`username` PK, `password_hash`, `created_at`），密码用 `hashlib.pbkdf2_hmac` 加盐哈希，**不存明文**。初始账号由 `config.json` 的 `admin` 段注入并在启动时 `CREATE TABLE IF NOT EXISTS` + seed，避免硬编码。
- **session/JWT**：选**服务端 session（签名 cookie）**，简单且可强制登出：
  - 登录成功 → 生成随机 `session_id`，存内存 session store（或签名 cookie 存 username+expiry），Set-Cookie `admin_session`。
  - 需鉴权路由用 `Depends(require_admin)` 校验 cookie 有效 + 未过期；无效则 `RedirectResponse` 到 `/admin/login`（对齐 V-M3-1「未登录访问管理页 → 重定向登录」）。
- **登录失败限速**：连续失败 5 次锁定 15 分钟（内存记录），防爆破。

### 2.3 操作审计日志（接入现有 `audit_log` 表）

- `audit_log` 表已存在（`operator, action, target, timestamp, request_id`），`db.py` 已有写入能力。
- `web_admin/audit.py` 封装：`log_action(operator, action, target, request_id)` 写入审计；`query_audit(filter)` 分页/按条件查询。
- 接入点：
  - **后台管理操作**：管理员登录/登出、启停 key、派发、改配置等 Web 操作 → 写审计（operator=管理员用户名）。
  - **服务端 WS/派发操作**：dispatch 等关键动作反查补写（对齐 SDD §9「谁在何时做了何操作」）。
- 审计页展示：按时间倒序，含操作者/动作/目标/时间/request_id，支持筛选（对齐 SDD V-M3-1「操作有审计日志」）。

### 2.4 路由清单

| 方法 | 路径 | 鉴权 | 功能 |
|------|------|------|------|
| GET | `/admin/login` | 公开 | 登录页（未登录访问其他页重定向至此） |
| POST | `/admin/login` | 公开 | 校验账号密码，设 session cookie |
| POST | `/admin/logout` | 需登录 | 登出，清除 session |
| GET | `/admin` | 需登录 | 仪表盘（设备数/在线数/派发统计） |
| GET | `/admin/audit` | 需登录 | 审计日志页（分页 + 筛选） |
| GET | `/admin/devices` | 需登录 | 设备列表/状态 |
| GET | `/api/admin/audit` | 需登录 | 审计 JSON API（前端拉取） |

> M3 本期不含完整 Web UI（D-2 web_admin 完整 UI 的完整版），聚焦鉴权 + 审计 + 基础仪表盘；花费看板（D-6）归后续批次（审计改进项已就位，V-M3-2/V-M3-3 花费相关在 D-6 单独验收）。

### 2.5 与现有 server/main.py 挂载

- 在 `main.py` 顶部 `include_router(web_admin.router, prefix="/admin")`（页面路由）+ `include_router(api_router, prefix="/api/admin")`。
- 不改变现有 `/api/devices`、`/api/usage`、`/api/dispatch/*`、`/ws` 路由，纯增量挂载。
- 新增 `admin_user` 表在 `db.py` `init_db` 中建表 + seed 初始管理员。

### 2.6 验收标准（对齐 SDD V-M3-1）

| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| AC-M3-1 | 未登录访问 `/admin` | 重定向到 `/admin/login`；登录后操作有审计日志 |
| AC-M3-2 | 错密码登录 ×5 | 第 6 次被锁 15 分钟（限速生效） |
| AC-M3-3 | 正确登录 + 做派发/改配置操作 | `audit_log` 表新增对应记录 |
| AC-M3-4 | `db.py` 新增 admin_user 表 | 建表成功，初始账号 seed，密码为哈希非明文 |
| AC-E4-1 | `sidecar.exe --first-run` | 生成 device.json + 落盘用户级 config.json |
| AC-E4-2 | `sidecar.exe --install-service` / `--uninstall-service` | 服务注册/移除成功（断言存在/不存在） |
| AC-E4-3 | `run` 读用户级 config | register 上报，device 入服务端注册表 online=1 |
| AC-E4-4 | `_load_config` 改造 | 优先读用户级，缺失用内嵌模板生成 |
| AC-E4-5 | 回归 | sidecar 18 / server 20 测试全过零回归 |

---

## 3. SDD 需补的 E-4 规范（写进 SDD）

在 SDD 补以下内容（新小节或并入现有节）：
- **§5 技术选型**：补充「Sidecar 安装集成」行——NSIS extraResources 内嵌 sidecar.exe + nssm.exe + 预置 config；`--install-service`/`--uninstall-service`/`--first-run` 子命令；用户级 `%PROGRAMDATA%\CherryManaged` 落盘。
- **新增 §5.x 安装集成与自动配对**：1.1-1.6 的内容（三段式、NSIS 装配、首启注册、自动配对四要素、卸载语义）。
- **§11.5 V-M4-1**：细化验收为 AC-E4-1~5 的具体测试动作。

## 4. 更新清单

| 文件 | 改动 |
|------|------|
| `docs/sdd-企业受管版.md` | §5 补 E-4 选型；新增安装集成规范节；V-M4-1 细化 |
| `server/web_admin/`（新建 4 模块 + templates） | M3 Web 后台实现 |
| `server/main.py` | include_router 挂载 web_admin |
| `server/db.py` | 新增 `admin_user` 表 + seed |
| `server/config.json` | 新增 `admin` 段（初始管理员） |
| `server/tests/` | 新增 web_admin 测试 |
| `sidecar/sidecar.py` | 新增 `--install-service`/`--uninstall-service`/`--first-run` 子命令 + `_load_config` 用户级改造 |
| `sidecar/scripts/build.spec` / `build_sidecar.sh` | 若需要支持新子命令打包 |
| `docs/cdd-企业受管版.md` | （已修表名漂移 00fe10d，本次无需再改） |

---

## 5. 验收方式（品控）

- E-4：本机 Linux 验证子命令逻辑（`--first-run`/`--install-service` 的 Linux 桩） + pytest 回归；Windows NSIS 集成走 CI 产物，真机验收归批次 F。
- M3：本机 pytest（web_admin 测试）+ 手动 curl 走登录/审计链路。

## 6. spec 契约（供看板 spec-submit）

```json
{
  "taskId": "JJC-20260812-001",
  "spec": {
    "purpose": "补齐 CherryStudio 企业受管版两处能力缺口：E-4 安装包集成让 Sidecar 随 Fork 安装包分发、首次启动自动注册 Windows 服务并自动连服务器配对（员工零手动配置）；M3 Web 管理后台为管理员提供登录鉴权与操作审计日志页面，对齐 SDD §1.1/§5/§9 规范。",
    "outputs": [
      "新增 sidecar 子命令 --install-service/--uninstall-service/--first-run，支持服务注册与首启配对",
      "sidecar _load_config 改造：优先读用户级 %PROGRAMDATA%\\CherryManaged\\config.json，缺失用内嵌模板生成落盘",
      "server/web_admin 模块（auth/router/audit + Jinja2 模板）：管理员登录鉴权 + 审计日志查询页",
      "server/db.py 新增 admin_user 表 + config.json admin 段 seed 初始账号",
      "server/main.py 挂载 web_admin 路由（/admin + /api/admin 前缀）",
      "SDD §5/新增安装集成节/V-M4-1 细化 E-4 规范"
    ],
    "acceptance_criteria": [
      "执行 sidecar --first-run 断言生成 device.json 并落盘用户级 config.json",
      "执行 --install-service 断言 NSSM 服务 CherrySidecar 存在且 StartType=Auto；--uninstall-service 断言服务移除",
      "sidecar run 读取用户级 config 后 register 上报，服务端 devices 表该设备 online=1",
      "未登录访问 /admin 断言重定向到 /admin/login；正确登录后操作断言写入 audit_log 表",
      "连续 5 次错密码登录断言第 6 次被锁定 15 分钟（限速生效）",
      "db.py 新增 admin_user 表断言初始账号 seed 且密码为 pbkdf2 哈希非明文",
      "回归断言：sidecar 18 测试 + server 20 测试全过零回归"
    ],
    "boundaries": [
      "E-4 只新增 sidecar 子命令与 config 落盘改造，不改动既有派发/采集/对账/自愈逻辑",
      "M3 本期聚焦鉴权 + 审计 + 基础仪表盘，不实现 D-6 花费看板（归后续批次）",
      "Windows NSIS 集成走 CI 产物验证，本机 Linux 仅验证子命令逻辑，真机验收归批次 F",
      "token 走 CI secret 编译进包，不硬编码进源码仓库",
      "管理员密码 pbkdf2 加盐哈希存储，明文不落盘"
    ],
    "dependencies": [
      "批次E已产出的 sidecar.exe 打包链路（build.spec/build_windows.yml）",
      "server 现有 main.py/db.py（audit_log 表已存在）",
      "SDD §1.1/§5/§9 规范作为 M3 实现依据",
      "batch C 已统一受管表 managed_entity（CDD 已对齐 00fe10d）"
    ]
  },
  "cdd": {
    "agents": [
      "主Agent(鮱澄)起草本方案并代军师落盘",
      "审计官(shenyi)审核方案通过后放行",
      "研发(daima)落地 server/web_admin + sidecar 子命令 + SDD 修订",
      "品控(shencha)验收 E-4/M3 验收标准"
    ]
  }
}
```
