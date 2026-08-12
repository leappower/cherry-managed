# JJC-20260812-001 · M3 Web 管理后台 + E-4 安装包集成 — 执行方案

> 2026-08-12 | 项目：`/home/chee/Projects/cherry-managed`（远端 leappower/cherry-managed）+ Fork 源码 `/home/chee/.openclaw/workspace-main/cherry-src`
> 权威定义：`任务分解-v4.0.md` D-2（Web 管理后台）+ E-4（安装包集成）
> 前置：M0/M1/M2 全完成（含批次 D 1302c57、批次 E dc587e7）
> 起草：主 Agent（军师子 Agent 长任务易中断，主 Agent 兜底产出）

---

## 一、范围界定

**本任务 = D-2 Web 管理后台 + E-4 安装包集成**

### 做
- **D-2 Web 管理后台**：管理员登录鉴权 + 管理 API + 管理页面 + 操作审计日志查看
- **E-4 安装包集成**：Fork 安装包集成 Sidecar（electron-builder extraResources）+ 受管标记 + 卸载器

### 需老板澄清（NEEDS CLARIFICATION）
- M3 全量是 D-1~D-10（含 D-6 花费监控 / D-7 skills_repo / D-8 patch_repo / D-9 gitbash_repo）。任务标题只提「Web 管理后台」→ **是否本轮只做 D-2（Web 后台），花费监控等归后续？**
- E-4 依赖 E-1（Windows 构建流水线，未做）。**E-4 现在做 Fork 侧集成配置（yml/nsis），还是等 E-1？**

---

## 二、现状核实（已实读代码）

### server/（D-1 骨架已建，批次 A）
- `main.py`（FastAPI + WS 2334）+ `ws_server.py` + `device_registry.py` + `dispatch.py` + `collect.py` + `db.py` + `feed.py`（批次 D）
- **audit_log 表已建**（db.py:93），`db.audit()` 已实现，collect/dispatch 全链路写入（D-2 操作审计基础就绪）
- **无管理员登录鉴权、无管理 UI**（D-2 主要缺口）
- 现有 API：`/api/devices`、`/api/dispatch_log`、`/api/usage`、`/api/reconcile`、`/api/release/publish`、`/api/dispatch/*`

### Fork 源码（E-4 集成点，cherry-src/electron-builder.yml）
- `win.target` = nsis + portable
- `nsis.include: build/nsis-installer.nsh`（安装/卸载脚本，卸载器集成点）
- `extraResources`（可加 sidecar.exe 到安装包）
- `win.extraResources`（win 特定资源）
- **E-4 集成点**：extraResources 加 sidecar.exe + nsis-installer.nsh 处理 Sidecar 服务卸载 + 受管标记（CHERRY_MANAGED_BUILD=1）

---

## 三、D-2 Web 管理后台方案

### 3.1 管理员登录鉴权
- 配置 `config.json` 加 `admin_user` / `admin_password_hash`（或 token）
- 登录 API `POST /api/admin/login` → 校验 → 发 token（HMAC/随机 session）
- 管理 API 需 token（依赖注入校验）
- 简单实现：单管理员 + 密码哈希（不引入重鉴权框架，符合服务端轻量）

### 3.2 管理 API（需鉴权）
| API | 说明 |
|-----|------|
| `GET /api/admin/devices` | 设备列表 + 在线状态 + 分组 |
| `GET /api/admin/dispatch_log` | 派发日志 |
| `GET /api/admin/usage` | 用量聚合 |
| `GET /api/admin/audit_log` | **操作审计日志查看（D-2 核心）** |
| `GET /api/admin/reconcile` | 对账汇总 |
| `GET /api/admin/agents` | 各设备 Agent 清单（透传） |
| `POST /api/admin/dispatch/*` | 派发（复用现有 dispatch 逻辑）|

### 3.3 管理页面
- 静态 HTML（`server/static/admin.html`）挂载于 `/admin`
- 登录页 + 主面板（设备/派发/用量/审计日志 Tab）
- 轻量无框架（原生 JS + fetch），避免引入前端构建链

### 3.4 操作审计
- 复用 audit_log 表（已就绪）
- 管理操作（登录/派发/发布）写 audit_log

### 3.5 测试
- `server/tests/test_admin.py`：登录鉴权、token 拒绝、审计日志、管理 API

---

## 四、E-4 安装包集成方案

### 4.1 集成点（cherry-src/electron-builder.yml）
```yaml
extraResources:
  - from: "<sidecar exe 路径>"
    to: "sidecar"          # 安装后 resources/sidecar/sidecar.exe
win:
  extraResources: ...       # 或 win 特定
```
- 把 Sidecar exe（批次 E 产物 dist/sidecar.exe，CI 构建）打进入安装包 resources/sidecar/

### 4.2 卸载器（build/nsis-installer.nsh）
- 卸载时停止 + 移除 Sidecar 服务（NSSM 卸载）
- 清理受管标记数据（managed_registry.db 可选保留/清除，需确认语义）

### 4.3 受管标记
- 受管安装：设置环境变量 `CHERRY_MANAGED_BUILD=1`（M1 遗留：受管运行时须设此 env，应用启动读）
- 或安装包带受管标记文件

### 4.4 依赖与阻塞
- **E-4 依赖 E-1（Windows 构建流水线，未做）**：sidecar.exe 需 CI 构建（build_windows.yml 批次 E 已就位，可触发）
- E-4 实际产出 Windows 安装包需 E-1 流水线跑通 + Windows 打包
- 本轮可做：electron-builder.yml extraResources + nsis 卸载脚本 + 受管标记配置（Fork 侧配置集成），实际打包验证归 E-1/M4

---

## 五、NEEDS CLARIFICATION（需老板拍板）

| # | 决策点 | 建议 |
|---|--------|------|
| CL1 | **M3 范围**：只做 D-2 Web 后台，还是含 D-6 花费监控/D-7 skills_repo/D-8 patch_repo/D-9 gitbash_repo？ | 本轮只做 D-2（Web 后台），花费监控 D-6 单独立项 |
| CL2 | **E-4 执行深度**：本轮做 Fork 侧配置集成（yml/nsis/受管标记，不实际打包），还是等 E-1 构建流水线？ | 本轮做配置集成 + 文档，实际打包归 E-1/M4 |
| CL3 | **管理员账号**：单管理员固定账号（config.json），还是要多用户？ | 单管理员（轻量，符合服务端骨架） |
| CL4 | **卸载时受管数据**：卸载器清理 managed_registry 还是保留？ | 卸载时清除（员工机退出受管则清标记） |
| CL5 | **E-4 受管标记方式**：环境变量 CHERRY_MANAGED_BUILD=1 vs 受管标记文件？ | 按 M1 已定：CHERRY_MANAGED_BUILD=1 环境变量 |

---

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| 引入重鉴权框架过重 | 单管理员 + token，轻量实现 |
| 管理 API 未鉴权暴露 | 全部管理 API 加 token 校验，未授权 401 |
| E-4 依赖 E-1 未做 | 本轮只做 Fork 配置集成，打包归 E-1/M4 |
| nsis 卸载脚本改动破坏官方安装器 | 增量补丁 + 保留原逻辑，真机验证归 M4 |
| 管理页面引入构建链 | 原生 HTML/JS 静态页，无构建 |

---

## 七、更新清单
- `server/config.json`（+admin_user/password_hash）
- `server/auth.py`（新增：登录 + token 校验）
- `server/main.py`（+管理 API + /admin 静态挂载）
- `server/static/admin.html`（新增：管理页面）
- `server/tests/test_admin.py`（新增）
- `cherry-src/electron-builder.yml`（+extraResources sidecar）
- `cherry-src/build/nsis-installer.nsh`（+Sidecar 服务卸载）
- `docs/批次F-M3Web后台+E4集成方案.md`（本方案）
- `docs/sdd-企业受管版.md`（管理后台 + 安装包集成节）

---

## 八、验收标准（待 SDD 契约细化后对齐）

| # | 验收项 | 通过标准 |
|---|--------|---------|
| AC1 | 登录鉴权 | 错误密码拒绝，正确密码发 token |
| AC2 | 管理 API 鉴权 | 无 token 401，有 token 200 |
| AC3 | 审计日志 | 登录/派发/发布写 audit_log，可查询 |
| AC4 | 管理页面 | /admin 可登录 + 查看设备/派发/用量/审计 |
| AC5 | E-4 集成配置 | electron-builder.yml extraResources 含 sidecar + nsis 卸载处理 |
| AC6 | pytest 回归 | server 既有 20 + 新增 admin 测试全过；sidecar 18 不回归 |

> ⚠️ 待老板下发 SDD 契约（若有精确 AC 将以契约为准，如批次 E 模式）
