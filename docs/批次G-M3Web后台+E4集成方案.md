# 批次G：M3 Web 管理后台补缺 + E-4 Sidecar 安装集成 方案（修订版 v2）

> 文档版本：v2.0 | 日期：2026-08-13 | 状态：review
> 项目：cherry-managed | 看板：JJC-20260812-001
> 前置：M2 批次A-E 完成 + 批次F（c61e622）已实现 D-2 Web 后台代码
> 本修订版修正 v1.0（b2065c0）的 M3 前提误判：**D-2 Web 管理后台代码已由批次F(c61e622)实现**，本批次 M3 改为「核查现有 D-2 + 补齐真缺口」；E-4 聚焦本仓库 Sidecar 代码缺口。

---

## 0. 现状核实（审计官交叉验证 + 主 Agent 实读，2026-08-13）

### ✅ 已实现（批次F c61e622，2026-08-12 21:38 作者鮱澄）
- **server/auth.py**（76行）：单管理员登录鉴权。`hash_password`/`verify_password` = PBKDF2-HMAC-SHA256 + 随机盐；`AdminAuth` = 登录发随机 session token + 内存 session 集合。**与 v1.0 方案 §2.2 设计逐字一致。**
- **server/main.py**：已挂载完整 `/api/admin/*` 路由（login/logout/devices/dispatch_log/usage/audit_log/reconcile/agents/dispatch·agent/provider/skills），全部 `Depends(require_admin)` token 鉴权；`/admin` 静态挂载 index.html。
- **server/static/admin/index.html**（233行）：原生 HTML/JS，登录 + 设备/派发/用量/审计/Agent/对账 Tab。
- **server/tests/test_admin.py**（10个测试）：登录鉴权/token拒绝/审计日志/管理API。
- **server/config.json**：`admin_user`/`admin_password_hash`（pbkdf2 哈希）单管理员。
- **SDD §14（D-2 Web 管理后台）+ §15（E-4 安装包集成契约）** 已写入 v1.1。
- **docs/批次F-M3Web后台+E4集成方案.md**：D-2 + E-4 执行方案（含 CL1-CL5 老板决策点）。

### ⚠️ 真缺口（本批次要做）
1. **E-4 Sidecar 代码（核心）**：c61e622 只做了 docs + Fork 侧(cherry-src/electron-builder.yml)配置记录；**本仓库 sidecar 缺失**：
   - `--install-service`/`--uninstall-service`/`--first-run` 子命令（仅剩现有 `run`/`probe`/`agents`/`models`/`deploy`）
   - `_load_config()` 用户级落盘改造（当前读 `_MEIPASS/config/sidecar.json` 只读 + device_id 硬编码）
   - 自动配对（server url/token 编译进包、设备标识自动生成、上报时序）
2. **D-2 缺限速**：现有登录无失败限速（审计官指出；v1.0 方案 AC-M3-2「5次锁15分钟」未实现）
3. **D-2 审计/设备页分页筛选**：现有扁平返回，无分页（审计官建议）

### ❌ 撤销的 v1.0 误判
- ~~新建 server/web_admin 模块~~（D-2 已实现）
- ~~新增 db.py admin_user 表 + seed~~（现有用 config 单管理员，避免双轨；采用「复用 config 单管理员」决策）
- ~~server 20 测试~~（实测 server 30 测试：test_server 10 + test_feed 10 + test_admin 10）

---

## Part 1: E-4 Sidecar 安装集成（本批次核心，代码缺口）

> 与批次F(c61e622)承接：批次F 已落 Fork 侧 electron-builder.yml extraResources + nsis 卸载脚本 + 受管标记(CHERRY_MANAGED_BUILD=1)配置；本批次补齐 **本仓库 sidecar 侧** 的安装/配对代码，使「sidecar.exe → 安装包 → 首启 → 自注册 + 自动配对」真正可运行。

### 1.1 总览
老板拍板：Sidecar 集成进 Fork 安装包（NSIS），员工安装后**首次启动自动注册 Windows 服务 + 自动连服务器配对（零手动配置）**。

三段式：
1. **构建期**：GitHub Actions windows-latest 出 `sidecar.exe`（已通 E-3）→ 并入批次F 已配的 Fork extraResources。
2. **首启期**：`sidecar.exe --first-run`（安装完成 NSIS 调起）生成设备标识 + 落盘用户级配置 + `--install-service` 注册 NSSM。
3. **配对期**：NSSM 服务跑 `run` → 读用户级 config → WS register → 服务端 devices 表 online=1 → 受管标记生效。

### 1.2 新增子命令（sidecar/sidecar.py）
| 子命令 | 语义 | 验收词 |
|--------|------|--------|
| `sidecar.exe --first-run` | 生成/读 device_id（MachineGuid+hostname hash 机器指纹）、落盘用户级 `%PROGRAMDATA%\CherryManaged\config.json`+`device.json`、触发安装服务 | 断言 device.json/config.json 生成 |
| `sidecar.exe --install-service` | 注册/更新 NSSM 服务 `CherrySidecar` 并启动（StartType=Auto, AppExit Restart, AppRestartDelay 5000, 日志重定向 logs/） | 断言服务存在+Auto+进程在跑 |
| `sidecar.exe --uninstall-service` | 停止并移除 NSSM 服务 | 断言服务不存在 |
| `sidecar.exe run`（已有） | 常驻主进程，改读用户级 config | 断言 register 上报、device 入服务端 |

**权限降级兜底**：首启提权失败（UAC）→ 降级「用户登录自启」（Run 注册表键 + 托盘）+ 写日志 + 下次自动补注册。
**幂等**：重复安装不报错，已存在则更新配置后重启服务（升级场景 stop/remove 重建指向新 exe）。

### 1.3 _load_config 用户级落盘改造
- **当前**：`_load_config()` 读 `Path(__file__).parent/config/sidecar.json`，PyInstaller onefile 下 `__file__`→`_MEIPASS` 只读。
- **改为**：优先读用户级 `%PROGRAMDATA%\CherryManaged\config.json`；不存在则用内嵌 `_MEIPASS/config/sidecar.json` 作为模板生成并落盘到用户级。兼容现有打包，支持运行时持久化。
- **device_id**：首启生成 = `managed-` + MachineGuid(`HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`) + hostname hash；`hostname=socket.gethostname()`、`os="windows"`、`group` 用内嵌默认。
- **server url/token**：构建时 CI 从 secrets(`SERVER_URL`/`DEVICE_TOKEN`) 写入打包 sidecar.json 的 server 段，**不硬编码进源码**。

### 1.4 自动配对时序
首启 → 生成/读 device_id → 落盘用户级 config → `--install-service` → NSSM 起 `run` → `_register()` 发 register → 服务端 `devices` 表 online=1 → 可派发 → 对账建受管标记 → 配对完成。**员工零输入**。

### 1.5 卸载器语义（承接批次F CL4 决策：卸载清除）
1. 正常卸载 Fork：`customUnInstall` 调 `--uninstall-service` + 删 `<PROGRAMDATA>\CherryManaged` 数据 → 彻底卸载。
2. 仅停用：`nssm stop CherrySidecar`，不删数据。
3. Fork 升级/重装：不动 Sidecar 服务，仅更新 exe/config（受管状态不丢，对齐 V-M4-2）。
- 卸载先停服务再删文件（Windows 不能删运行中 exe）。

### 1.6 与批次E衔接
- 保留 `sidecar.py`/`build.spec`/`build_windows.yml` 产出的 `sidecar.exe` 为唯一侧车二进制；E-4 只在其上新增子命令 + config 改造，**不改既有派发/采集/对账/自愈逻辑**（回归 sidecar 18）。
- NSIS 装配已由批次F 在 Fork 侧配好，本批次验证衔接（extraResources 指向 sidecar.exe 路径一致）。

### 1.7 E-4 风险与对策
| 风险 | 等级 | 对策 |
|------|------|------|
| 杀软误报（PyInstaller exe+NSSM+自启动+回连） | 🔴 高 | 证书签名；白名单指引文档；内嵌 config 不落敏感明文；首启日志可追踪 |
| config 落盘（_MEIPASS 只读/权限） | 🟡 中 | 用户级 %PROGRAMDATA%\CherryManaged；提权失败降级 Run 自启 |
| 多实例/同机多用户 | 🟡 中 | device_id 基于机器级 MachineGuid 同机唯一；服务名唯一 CherrySidecar；安装幂等 |
| 配对失败重试 | 🟡 中 | 复用 WS 指数退避（1s/60s）；register 失败保留 pending 定时重试；不阻塞常驻 |
| 卸载孤儿 | 🟡 中 | 正常卸载 --uninstall-service + 删数据；验收 V-M4-4 |
| NSSM 未内置 | 🟡 中 | extraResources 内置 nssm.exe；缺失报错留日志不静默 |

---

## Part 2: M3 Web 后台补缺（核查现有 D-2 + 增量）

### 2.1 现有 D-2 已覆盖（不做，避免回归）
登录鉴权(PBKDF2+token)、/api/admin 全家族、/admin 静态页、审计写库、test_admin 10测试。**全部保留。**

### 2.2 真缺口补丁
1. **登录失败限速**：`AdminAuth` 加失败计数（内存 dict：user→(count, lock_until)），连续 5 次失败锁 15 分钟；`login()` 校验锁定态。→ 对齐 v1.0 AC-M3-2。
2. **审计/设备分页筛选**：`/api/admin/audit_log`、`/api/admin/devices` 加 `limit/offset/action/operator` 查询参数，返回分页元数据（total/limit/offset）+ 列表。→ 对齐审计官建议。
3. **（明确决策）admin_user 表**：**不新增表**，复用现有 config 单管理员（避免双轨）。后续如需多用户再迁移（SDD §14 注明）。

### 2.3 路由（仅新增/增强，不冲突）
- 增强：`GET /api/admin/audit_log`（+分页筛选）、`GET /api/admin/devices`（+分页）
- auth.py：内部加限速逻辑（不新增路由）
- **无** /admin/login 等新页面路由（已有 /api/admin/login + /admin 静态页）

### 2.4 测试
- test_admin.py 增补：限速断言（6次锁15分钟）、分页断言。回归基准：server 30（test_server 10 + test_feed 10 + test_admin 10 + 新增）

---

## 3. SDD 修订
- §14 D-2：补「登录失败限速 5次锁15分钟」+「audit_log/devices 分页筛选」+「单管理员 config 方案（不建表）」
- §15 E-4：补本仓库 sidecar 侧子命令（--first-run/--install-service/--uninstall-service）+ _load_config 用户级落盘 + 自动配对四要素 + 卸载语义

## 4. 更新清单（修订后准确版）
| 文件 | 改动 |
|------|------|
| `sidecar/sidecar.py` | 新增 3 子命令 + `_load_config` 用户级落盘 + device_id 自动生成 |
| `sidecar/config/sidecar.json` | server url/token 占位（CI 注入） |
| `sidecar/scripts/build.spec` | 若需支持新子命令打包（hiddenimports/data） |
| `server/auth.py` | 登录失败限速（新增） |
| `server/main.py` | audit_log/devices 分页筛选（增强，不冲突） |
| `server/tests/test_admin.py` | 限速 + 分页测试（新增） |
| `docs/sdd-企业受管版.md` | §14 补限速/分页/单管理员；§15 补 sidecar 子命令+落盘+配对 |
| `docs/批次G-M3Web后台+E4集成方案.md` | 本方案（修订 v2） |

## 5. 验收标准（修订版）
| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| AC-E4-1 | `sidecar --first-run` | 断言生成 device.json + 落盘用户级 config.json（非 _MEIPASS） |
| AC-E4-2 | `--install-service`/`--uninstall-service` | 断言服务注册/移除（存在/不存在 + StartType=Auto） |
| AC-E4-3 | `run` 读用户级 config | 断言 register 上报，服务端 devices 表 online=1 |
| AC-E4-4 | `_load_config` | 断言优先读用户级；缺失用内嵌模板生成落盘 |
| AC-M3-1 | 未登录访问管理 API | 断言 401（无 token） |
| AC-M3-2 | 连续 6 次错密码 | 断言第 6 次被锁 15 分钟（限速生效） |
| AC-M3-3 | 正确登录 + 派发操作 | 断言 audit_log 新增记录 |
| AC-M3-4 | audit_log/devices 分页 | 断言 limit/offset 生效 + total 正确 |
| AC-REG | pytest 回归 | sidecar 18 + server 全量（30+新增）全过零回归 |

## 6. spec 契约（修订版，供 spec-submit）
```json
{
  "taskId": "JJC-20260812-001",
  "spec": {
    "purpose": "补齐 CherryStudio 企业受管版 E-4 Sidecar 安装集成代码缺口（随 Fork 安装包分发、首启自动注册 Windows 服务并自动连服务器配对，员工零手动配置），并核查现有 D-2 Web 管理后台(c61e622已实现)补充真缺口（登录失败限速、审计/设备分页筛选）。",
    "outputs": [
      "sidecar 新增 --first-run/--install-service/--uninstall-service 子命令",
      "sidecar _load_config 用户级落盘改造(优先读 %PROGRAMDATA%\\CherryManaged\\config.json，缺失用内嵌模板生成)",
      "device_id 自动生成(机器指纹 MachineGuid+hostname hash)",
      "server/auth.py 登录失败限速(5次锁15分钟)",
      "server/main.py audit_log/devices 分页筛选(limit/offset/action/operator)",
      "server/tests/test_admin.py 增补限速+分页测试",
      "SDD §14 补限速/分页/单管理员决策；§15 补 sidecar 子命令+落盘+配对规范"
    ],
    "acceptance_criteria": [
      "执行 sidecar --first-run 断言生成 device.json 并落盘用户级 config.json",
      "执行 --install-service 断言 NSSM 服务 CherrySidecar 存在且 StartType=Auto；--uninstall-service 断言服务移除",
      "sidecar run 读取用户级 config 后 register 上报，服务端 devices 表该设备 online=1",
      "未登录访问管理 API 断言 401(token 拒绝)；正确登录后派发断言写入 audit_log",
      "连续 6 次错密码登录断言第 6 次被锁定(5次锁15分钟 限速生效)",
      "audit_log/devices 分页断言 limit/offset 生效且 total 正确",
      "pytest 回归断言 sidecar 18 + server 全量(30+新增)全过零回归"
    ],
    "boundaries": [
      "E-4 只新增 sidecar 子命令与 config 落盘改造，不改动既有派发/采集/对账/自愈逻辑",
      "M3 不重建 D-2 既有实现(c61e622)，只补限速与分页两缺口",
      "复用 config 单管理员，不新增 admin_user 表(避免双轨)",
      "Windows NSIS 集成走批次F已配 Fork 侧 + CI 产物验证，本机 Linux 仅验证子命令逻辑，真机验收归批次 F",
      "token 走 CI secret 编译进包，不硬编码进源码仓库"
    ],
    "dependencies": [
      "批次F c61e622 已实现 D-2 Web 后台与本仓库 docs/Fork侧配置",
      "批次E 已产出 sidecar.exe 打包链路(build.spec/build_windows.yml)",
      "server 现有 auth.py/main.py/db.py(audit_log 表已存在)",
      "batch C 已统一受管表 managed_entity"
    ]
  },
  "cdd": {
    "agents": [
      "主Agent(鮱澄)修订方案并代军师落盘",
      "审计官(shenyi)审核通过后放行",
      "研发(daima)落地 sidecar 子命令+落盘改造 + auth限速 + 分页",
      "品控(shencha)验收 E-4/M3 验收标准"
    ]
  }
}
```
