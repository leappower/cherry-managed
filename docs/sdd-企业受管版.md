# 系统设计文档（SDD）— CherryStudio 企业受管版

> 文档版本：v1.0 | 日期：2026-08-07 | 状态：review
> 项目代号：cherry-managed | 维护者：研发主管 daima
> 前置方案：方案-企业受管版-v4.0.md（老板 2026-08-07 定稿）
> 概念设计：cdd-企业受管版.md（本 SDD 的基于）
> 任务分解：任务分解-v4.0.md（M0-M4 / F/S/D/E 任务）
> 技术地基：源码校验报告-v2.0.1.md（AgentService/ProviderService/ai_usage_record/API Gateway 能力）
> 收敛记录：质询记录-v4.0.md（D1-D20 决策来源）
> 性质：本 SDD 为受管系统的**系统设计规范契约（spec）**，供看板六合一门禁（spec-submit）评分验收。

---

## 1. 系统架构

### 1.1 模块划分

```
cherry-managed/
├── server/                              ← 管理服务端（端口 2334）
│   ├── main.py                          ← WS 服务入口
│   ├── ws_server.py                     ← 设备连接管理
│   ├── device_registry.py               ← 设备注册表 + 分组
│   ├── dispatch.py                      ← 派发调度（幂等）
│   ├── cost_monitor/                    ← 花费监控模块（v4.0 新增）
│   │   ├── aggregator.py                ← ai_usage_record 聚合看板
│   │   ├── anomaly.py                   ← 异常判定规则
│   │   ├── key_rotation.py              ← 停 key 重建流程
│   │   └── transit_bridge/              ← 中转服务对接组件（v4.0 补充）
│   │       ├── transit_client.py        ← 中转侧管理 API 客户端（停用/授权接口，若第三方提供）
│   │       ├── transit_manual.py        ← 人工兜底：无管理 API 时生成停用工单向管理员，回填确认
│   │       └── README.md                ← 中转侧对接说明（第三方管理 API 支持矩阵 + 停用流程）
│   ├── web_admin/                       ← Web 管理后台（鉴权+审计）
│   ├── agents_lib/                      ← Agent 模板库（版本管理）
│   ├── skills_repo/                     ← SKILLS 版本仓库
│   ├── lock_rules/                      ← 锁死规则库
│   ├── gitbash_repo/                    ← Git Bash 安装包
│   ├── patch_repo/                      ← Fork 安装包 + 增量 patch
│   ├── data/                            ← 数据仓库
│   └── config.json                      ← 服务端配置
├── fork/                                ← Fork 源码（改官方最新 cherry-studio）
│   ├── official/                        ← 官方镜像分支
│   ├── managed/                         ← Fork 改动分支
│   └── patches/                         ← 增量 patch
├── sidecar/                             ← 员工端常驻进程（NSSM）
│   ├── sidecar.py                       ← 主进程
│   ├── lib/
│   │   ├── ws_client.py                 ← WS 客户端
│   │   └── fork_client.py               ← Fork 管理路由客户端
│   ├── dispatch.py                      ← 派发执行器（幂等）
│   ├── collect.py                       ← usage 采集上报 + 工作目录采集（agent-files）
│   ├── reconcile.py                     ← 对账
│   ├── managed_registry.py              ← 受管标记旁路表
│   ├── selfheal.py                      ← 自愈
│   └── config/sidecar.json              ← 本机配置
├── tools/
│   └── package-agent/                   ← Agent 打包（agent.json+skills+mcp+data）
└── docs/
    ├── 方案-企业受管版-v4.0.md           ← 最终方案（单一事实源）
    ├── cdd-企业受管版.md                 ← 本 CDD
    ├── sdd-企业受管版.md                 ← 本 SDD
    ├── 任务分解-v4.0.md                 ← 任务清单
    ├── 质询记录-v4.0.md                 ← 收敛过程
    └── 源码校验报告-v2.0.1.md           ← 技术地基
```

---

## 2. 接口设计（`/v1/admin/*` 管理路由）

> 鉴权：独立管理 key（`feature.api_gateway.managed_key`），bearer timing-safe 比对；与普通 API key 分离，员工拿到普通 key 调管理路由返回 401。
> 实现：在官方 `v1Routes` Elysia 插件链追加 `adminRoutes` 插件，复用 AgentService/ProviderService 公开方法，**不直写 sqlite**（D20）。
> 全部路由的通用响应：成功 200 + JSON body；鉴权失败 401；参数/校验失败 400；受管保护拒绝 403；未找到 404。

### 2.1 Agent 管理（对接 AgentService CRUD）

| 方法 | 路径 | 请求 | 响应（200） |
|------|------|------|------------|
| POST | `/v1/admin/agents` | `{name,type,model,instructions,configuration,tools,skills}`，`managed:true` 写入旁路表 | `{id,managed:true}` |
| GET | `/v1/admin/agents` | — | `[{id,name,type,model,managed,...}]` |
| GET | `/v1/admin/agents/:id` | — | `{id,name,type,model,instructions,...}` |
| PUT | `/v1/admin/agents/:id` | 全量字段，受管项校验 | `{id,...}`（受管项更新需管理 key） |
| DELETE | `/v1/admin/agents/:id` | — | `{ok:true}`（受管保护：非 managed_registry 写者不可删受管项） |
| POST | `/v1/admin/agents/:id/disable` | — | `{ok:true}`（软删/下架，员工端不可见不可用） |
| POST | `/v1/admin/agents/reorder` | `{ids:[...]}` | `{ok:true}` |

### 2.2 Provider / 模型管理（对接 ProviderService CRUD）

| 方法 | 路径 | 请求 | 响应（200） |
|------|------|------|------------|
| POST | `/v1/admin/providers` | `{name,type,base_url,models,api_key?}` | `{id}` |
| POST | `/v1/admin/providers/batch-upsert` | `{providers:[...]}` | `{upserted:n}` |
| PUT | `/v1/admin/providers/:id` | 更新字段（受管保护：只更新受管 provider，不动员工自配） | `{id}` |
| DELETE | `/v1/admin/providers/:id` | — | `{ok:true}`（受管项仅服务端可删） |
| POST | `/v1/admin/providers/:id/api-keys` | `{api_key}`（addApiKey，停 key 重建流程用） | `{ok:true}` |
| PUT | `/v1/admin/providers/:id/api-keys` | `{api_keys:[...]}`（replaceApiKeys，重建后推新 key） | `{ok:true}` |
| DELETE | `/v1/admin/providers/:id/api-keys` | `{api_key}`（仅删员工端 provider 的 key 字段；key 归中转侧，Fork 无法停用中转侧 key） | `{ok:true}` |

### 2.3 SKILLS / MCP 管理

| 方法 | 路径 | 请求 | 响应（200） |
|------|------|------|------------|
| GET/POST/PUT/DELETE | `/v1/admin/skills` | agent_skill 表 CRUD + 版本写 agent_global_skill | `{ok:true}` 或对象 |
| GET/POST/PUT/DELETE | `/v1/admin/mcp` | mcp_server 表 CRUD | `{ok:true}` 或对象 |

### 2.4 Usage / 工作目录读取（供 Sidecar 采集）

| 方法 | 路径 | 请求 | 响应（200） |
|------|------|------|------------|
| GET | `/v1/admin/usage` | `?from=&to=&device_id=` | `[{providerId,modelId,inputTokens,outputTokens,totalTokens,sourceType}]`（读 ai_usage_record 表） |
| GET | `/v1/admin/agent-files` | `?agent_id=&path=` | 枚举/读取 Agent 工作目录（accessible_paths 内）上下文与产出内容 |

### 2.5 受管保护语义（泛化）

- `isManaged(id)` → 查旁路表 managed_registry；
- 管理路由对受管项的变更需校验请求方为 Sidecar/服务端（管理 key 保证）；
- 渲染层对受管项隐藏删除/编辑（见锁死 UI）；
- 员工手动改（绕过管理路由）由 Sidecar 对账发现并修复。

---

## 3. WS 消息协议（服务端 ↔ Sidecar，端口 2334）

> 框架：服务端 `websockets`；Sidecar `websocket-client`。JSON `type` 区分。断线指数退避重连；重连后补传未确认指令（幂等）。

### 3.1 register（Sidecar → 服务端）
```json
{"type":"register","device_id":"HOST_CHEN-001","hostname":"HOST_CHEN","os":"windows","cherry_version":"2.0.1","fork_version":"4.0.0-rc.1","git_bash_installed":false,"git_bash_version":null,"token":"***"}
```

### 3.2 dispatch_agent（服务端 → Sidecar）
```json
{"type":"dispatch_agent","action":"create","agent":{"name":"企_客服助手","managed":true,"model":"企_DeepSeek:deepseek-v4-flash","instructions":"...","accessible_paths":["D:\\...\\Agents\\deployed"],"configuration":{"permission_mode":"bypassPermissions","max_turns":100},"tools":["web_search","bash"],"skills":["skill-a@v1.2","skill-b@v2.0"]},"package_url":"http://server/agents/企_客服助手/v3.zip","request_id":"req-001"}
```

### 3.3 dispatch_provider（服务端 → Sidecar，R1）
```json
{"type":"dispatch_provider","action":"add","provider":{"name":"企_DeepSeek","type":"openai","api_key":"***","base_url":"https://...","models":["deepseek-v4-flash","deepseek-v4-pro"]},"request_id":"req-002"}
```

### 3.4 sync_lock_rules（服务端 → Sidecar，R5）
```json
{"type":"sync_lock_rules","rules":{"locked_pages":["provider","model","mcp","skills","agent"],"hidden_keys":true,"readonly_fields":["api_key","base_url"]},"version":3}
```

### 3.5 usage（Sidecar → 服务端）
```json
{"type":"usage","device_id":"HOST_CHEN-001","period":"2026-08-07T09:00:00Z/2026-08-07T10:00:00Z","records":[{"provider":"企_DeepSeek","model":"deepseek-v4-flash","input_tokens":1200,"output_tokens":800,"total_tokens":2000}],"errors":[]}
```

### 3.6 dispatch_result（Sidecar → 服务端）
```json
{"type":"dispatch_result","request_id":"req-001","success":true,"agent_id":"agent_xxx","error":null}
```

### 3.7 status（Sidecar → 服务端）
```json
{"type":"status","device_id":"HOST_CHEN-001","online":true,"agents":["企_客服助手","企_设计助手"],"cherry_healthy":true,"cherry_online":false,"cherry_version":"2.0.1","fork_version":"4.0.0-rc.1","git_bash_ready":false,"last_sync":"2026-08-07T10:00:00Z"}
```

### 3.8 fetch_patch / install_gitbash（服务端 → Sidecar）
```json
{"type":"fetch_patch","cherry_version":"2.1.0","request_id":"req-003"}
{"type":"install_gitbash","url":"http://server/gitbash/Git-latest.exe","request_id":"req-004"}
```

### 3.9 幂等与可靠性
- 每条派发指令带 `request_id`，服务端记录已派发状态，Sidecar 回执后确认；
- 断线重连后，服务端重发未确认指令（幂等，不重复创建）；
- 离线设备指令入队，重连后补发（D7/D9）。

---

## 4. 数据模型

### 4.1 managed_registry（本地旁路表，sqlite，Sidecar 唯一写者）

```sql
CREATE TABLE managed_registry (
  id TEXT PRIMARY KEY,      -- provider/agent/skill/mcp 的 id
  type TEXT NOT NULL,       -- 'provider' | 'agent' | 'skill' | 'mcp'
  managed INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);
```
- 不动官方 schema，无迁移锁库风险（Q7/Q-A3）。

### 4.2 ai_usage_record（官方原生表，Fork 读取）

| 字段 | 说明 |
|------|------|
| providerId | 提供商 id |
| modelId | 模型 id |
| inputTokens | 输入 token |
| outputTokens | 输出 token |
| totalTokens | 总 token |
| sourceType | 来源类型 |

### 4.3 服务端数据仓库（SQLite 起步 → PostgreSQL 规模化）

| 表 | 关键字段 |
|----|---------|
| devices | device_id(PK), hostname, os, cherry_version, fork_version, online, last_seen, group, token |
| dispatch_log | request_id, device_id, type, action, status(pending/success/fail), created_at |
| usage_agg | device_id, provider, model, input_tokens, output_tokens, total_tokens, period |
| agent_files | device_id, agent_id, path, content, captured_at |
| audit_log | operator, action, target, timestamp, request_id |

---

## 5. 技术选型落地

| 组件 | 选型 | 落地要点 |
|------|------|---------|
| 服务端框架 | Python FastAPI + uvicorn | 端口 2334，异步 WS |
| Web 后台 | FastAPI + Jinja2 + 简单前端 | 管理员登录鉴权 + 操作审计日志 |
| WS | `websockets`（服务端）/ `websocket-client`（Sidecar） | 长连接 + 心跳 |
| 数据存储 | SQLite（起步）→ PostgreSQL（规模化） | 起步简单可扩展 |
| Sidecar 常驻 | Python + NSSM（Windows 服务） | 开机自启、崩溃自愈、与 Fork 解耦 |
| Sidecar 打包 | PyInstaller → 独立 Windows exe | 员工机无 Python |
| Fork 构建 | GitHub Actions windows-latest runner + electron-builder（NSIS） | 本机 Linux 无 Wine/Windows 打包链，走 CI |
| 更新通道 | electron-updater generic provider（自建 feed） | 替代官方 releases.cherry-ai.com |
| Fork 语言/栈 | 跟随官方（Electron + Elysia + drizzle + libsql + TanStack Query） | 最小 diff，随官方栈 |

---

## 6. 热更新机制设计（方案 A IPC 广播 + 方案 C 兜底）

### 6.1 链路（方案 A）
```
Fork 管理路由（服务端/Sidecar 侧）
  → 写 sqlite（复用 AgentService/ProviderService，触发官方内部变更事件）
  → 主进程补 IPC 刷新广播（新 channel data-changed，携带刷新目标）
  → 渲染进程监听 → invalidateQueries('/agents' | '/providers' | ...)
  → TanStack Query 重新拉取 → UI 即时刷新（不重启）
```

### 6.2 验收标准（M0-1 必测）
1. 改 Agent 名/提示词 → UI 即时更新；
2. 派发 provider → 模型下拉即时可选；
3. 停 key 重建推新 key → 受管 provider 生效，员工端无需重启。

### 6.3 兜底（方案 C）
- 若 IPC 广播链路实测做不出（M0-1 失败），降级为「强制重启兜底」：管理路由写完 sqlite 后触发 CherryStudio 主进程重启（或提示员工重启）；
- 热更新为老板硬要求，方案 A 优先；方案 C 仅作技术兜底，需如实汇报。

### 6.4 一致性
- 管理路由写完 sqlite 主动广播 UI 刷新，UI 短暂延迟（秒级）可接受；
- Sidecar 周期性对账兜底，修复 UI 与服务端不一致。

---

## 7. 锁死 UI 设计（源码级 managed 判断 + lock_rules 远程迭代）

### 7.1 两层设计
- **机制固定（源码级）**：Fork 渲染组件加 `managed` 判断，受管项隐藏删除按钮/只读/隐藏 apiKey。编译期固定，随 Fork 版本更新。
- **规则远程迭代**：`lock_rules.json` 从服务端拉取，控制「哪些页面锁/隐藏哪些字段/只读哪些字段」等策略参数（非机制本身）。

### 7.2 源码级 managed 判断注入点（v2.0.1 基线；实施 F-10 必须重新核对 M0-3 最新官方基线）

| 位置 | 官方现状 | Fork 改法 |
|------|---------|----------|
| Agent 删除按钮 | Sessions.tsx `canDelete={!!workspaceId}` | 加 `&& !isManaged(agent.id)` |
| Agent 编辑 | Agent 配置页可改 | 受管项只读（disabled） |
| Provider 删除 | useProviderDelete.ts → deleteProviderById | 受管 provider 不显示删除入口 |
| Provider Key 显示 | 渲染层显示 apiKey 原文 | 受管 provider 隐藏/掩码 key（R2） |
| SKILLS / MCP 删除 | 设置页可删 | 受管项隐藏删除入口 |
| Model 下拉 | provider.models 可选 | 受管 provider 模型列表由服务端控制 |

### 7.3 受管判定
- 渲染层经 preload 暴露的 IPC 读旁路表 managed_registry.db；
- `isManaged(id)` 查旁路表；受管项 → 锁死；员工自配（不在 registry）→ 完全自由。

---

## 8. 花费监控模块（R2 兜底核心）

### 8.1 数据源
- 权威数据源：员工端 `ai_usage_record` 表（老板决策 A 确认走员工端表，非中转账单）；
- Fork `/v1/admin/usage` 读出，Sidecar 定时拉取 → 上报服务端数据仓库；
- 中转侧边界：key 归中转侧持有，Fork 无法控制中转侧 key 停用，走中转侧对接通道或人工兑底。

### 8.2 聚合看板
- 按 provider/model/设备/时间聚合；展示总 token、估算花费（单价表配置）、设备排行、模型排行；异常高亮（红色标记触发阈值项）。

### 8.3 异常判定规则（多维度防误判）
| 规则 | 触发 |
|------|------|
| 单设备超限 | 某设备累计 token/花费超阈值（阈值可配，如单设备日花费 > X 元） |
| 增速异常 | 某设备/模型 token 增速超正常波动（环比/同比 > 设定倍数） |
| 总额超限 | 全公司某 provider/model 累计超预算 |
| 关键模型异常 | 某企_模型被高频调用（疑似滥用，调用频次超限） |

### 8.4 停 key 重建流程
```
1. 触发异常 → 2. 服务端告警（Web 高亮 + 通知管理员）→ 3. 管理员确认（人工确认，防误判）
4. 停旧 key：走中转侧对接通道停用该设备企_ provider key
   ├── 优先：中转侧管理 API（若第三方提供 /admin 停用/授权接口）
   └── 兜底：人工在中转平台停用（无管理 API 时）
5. 新建 key：中转侧新建 → 6. 推新 key：Fork PUT /v1/admin/providers/:id/api-keys（replaceApiKeys，只更新受管 provider key 字段）
7. 热更新：IPC 广播 → 受管 provider 生效 → 8. 回执 + 审计日志
```

### 8.5 降级策略
- 停 key 期间员工暂时用自配模型（R1 双向并存的天然缓冲）；
- 进行中会话中断（可接受，异常滥用场景）；
- 重建为手动确认（人工判断），不自动盲目停 key；
- 中转侧无管理 API 时停 key 退化为「人工在中转平台停用 + 生成停工单向管理员回填确认」，停用完成前该设备企_ provider 可能仍可用——如实记录兑底窗口。

---

## 9. 安全与合规（非功能）

| 项 | 设计 |
|----|------|
| 独立管理 key | `feature.api_gateway.managed_key` 与普通 key 分离，bearer timing-safe；Sidecar 持管理 key 加密存储不落明文 |
| Web 后台鉴权 | 管理员账号登录 + 操作审计日志（谁在何时做了何操作）+ 设备 token 可轮换 + request_id 全链路追踪 |
| 派发幂等 | request_id + 回执 + 断线重发不重复创建 |
| R6 合规边界 | 采集含上下文与产出内容（工作目录全量），工作软件合规；传输走 WS 2334（可升级 WSS/TLS）；服务端加密存储；采集限 accessible_paths 内，不越权读工作目录外个人文件 |
| 数据一致性 | Sidecar 周期性对账（缺的补/受管保护/非受管忽略），Web 后台可见不一致告警 |

---

## 10. 里程碑 M0-M4 实施顺序与依赖

### 10.1 依赖路径
```
M0-1(热更新实测) ──→ F-9(热更新广播) ──→ M1 完成
M0-2(Windows构建) ──→ S-10/E-1/E-3 ──→ M2/M4
M0-3(官方基线) ──→ F-1(Fork分支) ──→ F-2~12 ──→ M1
F-7(usage路由) ──→ S-6(usage采集) ──→ D-6(花费监控) ──→ M3
```
**关键路径**：M0-3 → F-1 → F-2 → F-4/F-5 → M0-1(热更新验证) → F-9 → M1 → S-1~6 → M2 → D-6 → M3。

### 10.2 里程碑实施顺序

| 里程碑 | 前置 | 交付判据 | 关键任务 |
|--------|------|---------|---------|
| **M0** | 需 Fork 最小改动 + GitHub Actions + 网络 | V-M0-1~V-M0-5 全绿；V-M0-1 红时按方案 C 降级重评 | M0-1~M0-5 |
| **M1** | M0 通过 | V-M1-1~V-M1-6 全绿 | F-1~F-12 |
| **M2** | M1 完成 + M0-2 | V-M2-1~V-M2-8 全绿 | S-1~S-10 |
| **M3** | M2 完成 | V-M3-1~V-M3-6 全绿 | D-1~D-10 |
| **M4** | M1~M3 全绿 | V-M4-1~V-M4-4 全绿（含 R1-R7 七条主链路 7/7） | E-1~E-4 |

---

## 11. 验收标准（逐里程碑可执行判据，引用方案第十三节）

> 验收原则：每个里程碑验收 = 具体测试动作 + 通过条件 + 验收方式，不依赖主观判断。热更新、锁死 UI、花费监控等老板硬要求有单独验收项。验收由品控官（shencha）执行，通过后推进看板。

### 11.1 M0 求证验收
| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| V-M0-1 | 测试机跑 Fork，改 Agent 名/提示词 + 派发 provider | UI 不重启即时更新；模型下拉即时可选 |
| V-M0-2 | GitHub Actions windows-latest 跑 electron-builder | 产出 NSIS 安装包，可安装启动 |
| V-M0-3 | cherry-src 拉 origin/main，建 managed 分支 | 分支存在，rebase 干净无冲突 |
| V-M0-4 | 调 /v1/admin/agents POST 创建 | 创建成功，事务/校验不破坏，UI 刷新 |
| V-M0-5 | 调 /v1/admin/usage | 读出模型+token 用量，字段完整 |

### 11.2 M1 Fork 层验收
| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| V-M1-1 | 逐条调 agents/providers/skills/mcp/usage/agent-files CRUD | 全返回 200，数据正确写入 sqlite |
| V-M1-2 | 用普通 API key 调管理路由 | 401 拒绝；用管理 key 200 |
| V-M1-3 | 用非受管写者改/删受管项 | 被拒（错误码明确） |
| V-M1-4 | 管理路由写库后 | UI 即时刷新（复用 V-M0-1） |
| V-M1-5 | 受管 provider/agent 删除按钮 | 隐藏/禁用；员工自配项不受影响 |
| V-M1-6 | 改 feedURL + autoUpdate 配置 | 员工点升级不连官方 releases.cherry-ai.com |

### 11.3 M2 Sidecar 闭环验收
| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| V-M2-1 | Sidecar 启动连 WS 2334 | 服务端设备注册表出现，状态在线 |
| V-M2-2 | 服务端派 dispatch_agent | 员工端 Agent 出现，可收回/禁用/升级 |
| V-M2-3 | 服务端派 dispatch_provider | 模型下拉出现，key 生效，员工看不到 key 原文 |
| V-M2-4 | 员工手动改/删受管项 | Sidecar 对账修复，Web 后台可见告警 |
| V-M2-5 | 定时拉 /v1/admin/usage 上报 | 服务端数据仓库有记录 |
| V-M2-6 | 服务端拉 /v1/admin/agent-files | 读出 Agent 工作目录上下文+产出，限 accessible_paths 内 |
| V-M2-7 | 模拟 Fork 升级失败 | 回滚上一版，Sidecar 不崩 |
| V-M2-8 | 断开 WS 再连 | 重连成功，指令幂等不重复创建 |

### 11.4 M3 服务端 + 花费监控验收
| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| V-M3-1 | 未登录访问管理页 | 重定向登录；操作有审计日志 |
| V-M3-2 | 造 token 数据 | 按 provider/model/设备聚合正确，异常高亮 |
| V-M3-3 | 触发单设备超限/增速异常 | 告警触发，Web 高亮 + 通知管理员 |
| V-M3-4 | 管理员确认后走停 key 重建流程 | 中转侧停旧 key → 建新 key → 推员工端新 key → 热更新生效 |
| V-M3-5 | 调 transit_bridge | 有管理 API 走自动停用；无 API 生成停工单人工兑底 |
| V-M3-6 | 分组派发 + 幂等 | 按组下发，断线重发不重复创建 |

### 11.5 M4 端到端验收
| ID | 测试动作 | 通过条件 |
|----|---------|---------|
| V-M4-1 | 全新 Windows 装 Fork 包 | 安装成功，Sidecar 自动注册，受管标记生效 |
| V-M4-2 | 发新版本触发升级 | 员工端自动从自建 feed 升级，受管状态不丢 |
| V-M4-3 | 跑 R1-R7 七条主链路 | 7/7 通过 |
| V-M4-4 | 分别测三种卸载 | 符合选定语义 |

---

## 12. S.U.P.E.R 健康评估

| 原则 | 检查项 | 结论 |
|------|--------|------|
| Single Purpose | 每层（Fork/Sidecar/服务端）各解决一类职责 | ✅ 通过 |
| Unidirectional Flow | 派发：服务端→Sidecar→Fork→渲染；采集：Fork→Sidecar→服务端，单向 | ✅ 通过 |
| Ports over Implementation | /v1/admin/* 路由 + WS 协议定义接口契约 | ✅ 通过 |
| Environment-Agnostic | 配置走 config.json / sidecar.json / env，无硬编码 | ✅ 通过 |
| Replaceable Parts | Sidecar 与 Fork 解耦互不影响；Fork 升级失败回滚 | ✅ 通过 |

---

## 13. 变更记录

| 版本 | 日期 | 变更内容 | 来源 |
|------|------|---------|------|
| v1.0 | 2026-08-07 | 初始创建，基于方案 v4.0 最终版 + CDD + 任务分解 + 质询 + 源码校验报告 | 研发主管 daima |