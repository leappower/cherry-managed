# 概念设计文档（CDD）— CherryStudio 企业受管版

> 文档版本：v1.0 | 日期：2026-08-07 | 状态：review
> 项目代号：cherry-managed | 维护者：研发主管 daima
> 前置方案：方案-企业受管版-v4.0.md（老板 2026-08-07 定稿）
> 任务分解：任务分解-v4.0.md（M0-M4 / F/S/D/E 任务）
> 收敛记录：质询记录-v4.0.md（4 轮 v2 Fork 反向质询收敛）
> 技术地基：源码校验报告-v2.0.1.md
> 文档链：CDD（概念/协作/边界）→ SDD（系统设计规范契约 spec）→ 任务分解

---

## 1. 项目概述

**一句话定位**：CherryStudio 企业受管版是把内部员工 Windows 上的 CherryStudio 改造成**公司可远程统一管理**的受管分发系统——通过 Fork 官方最新版加管理路由 + 常驻 Sidecar + 服务端调度中枢三层协作，实现模型统一管控（R1）、Key 保护（R2）、Agent 远程管理（R3）、SKILLS 派发与版本管理（R4）、锁死 UI（R5）、数据采集（R6）、升级免疫（R7）。

**核心价值主张**：让 IT/管理员能对全公司员工端的 CherryStudio 做「模型可派可收、Key 不可见、Agent/SKILLS 可远程管、UI 可锁死、用量可监控、升级不破坏受管」的全生命周期管控，同时员工仍可自由自配模型与 Agent（双向并存）。

**项目范围**：受管分发系统本身（Fork 层管理路由 + Sidecar 常驻 + 服务端调度 + 花费监控 + Web 后台 + 构建发布流水线）。不含从零开发官方 CherryStudio（跟随官方最新 + 增量 patch 适配）。

**方向定案（老板 2026-08-07 拍板，v4.0 推翻 v3.1）**：
- **决策 A**：不用模型网关。R1+R2 靠「直连 provider 派发 key + 花费监控异常停 key 重建」兜底。
- **决策 B**：不锁定 v1.9.x。跟随官方最新版，官方更新后由我们 Fork 适配。
- **补充**：中转 = 第三方中转服务商，key 归中转侧持有，停 key 走中转侧对接通道，Fork 只能更新员工端 provider 的 key 字段。

---

## 2. 核心概念

| 概念 | 定义 | 落地形态 |
|------|------|---------|
| **受管 / 非受管** | 受管 = 公司统一派发并保护的对象（provider/Agent/SKILLS/MCP）；非受管 = 员工自配，完全自由，不在管控范围 | managed_entity 旁路表判定 |
| **managed_entity** | 本地 sqlite 旁路表（表名 managed_entity，修订对齐批次 C 决策 iii），记录 (kind,id)→受管映射，Sidecar 唯一写者，不动官方 schema，无锁库/迁移风险 | `managed_registry.db`（文件名保留） |
| **Fork 管理路由** | 在官方 `/v1` API Gateway（Elysia v1Routes 链）追加 `/v1/admin/*` 插件，供 Sidecar/服务端管理员工端 | `adminRoutes` 插件，复用 Service 方法 |
| **独立管理 key** | `feature.api_gateway.managed_key`，与普通 API key 分离，bearer timing-safe 比对，员工拿到普通 key 也无法调管理路由 | 独立鉴权 |
| **Sidecar** | 员工端独立常驻进程（NSSM Windows 服务），连服务端 WS + 调 Fork 管理路由 + 派发 + 采集 + 对账 + 自愈 | Python + PyInstaller → exe |
| **WS 协议** | 服务端↔Sidecar 长连接（端口 2334），JSON `type` 区分，心跳/派发/回执/状态/采集/补丁 | websockets / websocket-client |
| **花费监控** | 服务端聚合员工端 `ai_usage_record` 用量，按阈值/增速/单设备超限判定异常，异常走停 key 重建 | cost_monitor 模块 |
| **中转对接** | 中转侧（第三方中转服务商）管理 API 客户端（停 key/授权）+ 人工兜底（无 API 时停工单兑底） | transit_bridge 组件 |
| **热更新** | 管理路由写 sqlite → IPC 广播 → 渲染进程 invalidateQueries → UI 即时刷新（不重启） | 方案 A；兜底方案 C 强制重启 |
| **锁死 UI** | Fork 源码级改渲染组件加 `managed` 判断（隐藏删除/只读/隐藏 key）+ lock_rules 远程迭代策略参数 | 机制固定 + 规则远程化 |
| **R6 采集** | 采集整个智能体工作目录（Agent 的上下文与产出内容 + 用量），工作软件合规；限 accessible_paths 内，不越权读工作目录外个人文件 | `/v1/admin/agent-files` + usage |
| **升级免疫** | 禁用官方更新通道（改 feedURL 指向自建）+ Fork 增量 patch + 自动构建流水线 + 按需升级节奏 | electron-updater generic feed |

---

## 3. 三层协作框架

```
┌─────────────────────────────────────────────────────────────────────┐
│  服务端 server/  (FastAPI · 端口 2334)                                │
│  ├── Web 管理后台（管理员登录鉴权 + 操作审计日志）                      │
│  ├── 花费监控模块（ai_usage_record 聚合看板 + 异常告警 + 停 key 重建）  │
│  ├── agents_lib / skills_repo / lock_rules / gitbash_repo / patch_repo│
│  ├── 设备注册表 + 设备分组 + 派发幂等（request_id）                    │
│  └── 数据仓库（员工端用量聚合存储）                                    │
└──────────▲───────────────────────────────────────▲───────────────────┘
           │ WS(2334) 长连接                          │ WS(2334) 长连接
┌──────────┴────────────┐                ┌──────────┴────────────┐
│ 员工设备 A             │      ...      │ 员工设备 N             │
│ ┌──────────────────┐  │                │ ┌──────────────────┐  │
│ │ Fork 层（改官方    │  │                │ │ Fork 层          │  │
│ │ 最新 cherry-studio）│  │                │ │（同 A）          │  │
│ │  /v1/admin/* 路由  │  │                │ └────────▲─────────┘  │
│ │  独立管理 key      │  │                │          │ HTTPS       │
│ │  泛化受管保护      │  │                │ ┌────────┴─────────┐  │
│ │  热更新 IPC 广播   │  │                │ │ Sidecar (NSSM)   │  │
│ │  锁死 UI（源码级） │  │                │ │  WS 客户端        │  │
│ │  禁官方更新通道    │  │                │ │  派发/采集/对账    │  │
│ └────────▲─────────┘  │                │ │  managed_entity  │  │
│          │ HTTPS 127.0.0.1             │ │  自愈             │  │
│ ┌────────┴─────────┐  │                │ └──────────────────┘  │
│ │ Sidecar (NSSM)   │  │                └───────────────────────┘
│ │  WS 客户端 + Fork │  │
│ │  管理路由客户端    │  │
│ └──────────────────┘  │
└───────────────────────┘
```

### 3.1 三层职责边界

| 层 | 职责 | 不做什么 |
|----|------|---------|
| **Fork 层**（核心） | 管理路由 `/v1/admin/*`、独立管理 key、泛化受管保护、热更新 IPC 广播、锁死 UI（源码级）、禁官方更新通道 | 不直写 sqlite（走 Service 方法，D20）；不做模型网关 |
| **Sidecar 层**（常驻执行器） | 连服务端 WS + 调 Fork 管理路由 + 派发 Agent/模型/SKILLS + usage/工作目录采集上报 + 对账 + 受管旁路表 + 自愈 + NSSM 服务 | 不替 Fork 写官方 schema；不自作主张改员工自配项 |
| **服务端**（调度中枢） | Web 后台鉴权+审计、花费监控+停 key 重建、agents_lib/skills_repo/lock_rules/gitbash_repo/patch_repo、设备注册+分组+派发幂等、数据仓库、中转对接 | 不是运行依赖（服务端宕机员工端功能不受影响，恢复后 Sidecar 补传） |

### 3.2 数据流

```
派发流:  服务端 dispatch → WS dispatch_* (request_id) → Sidecar 调 Fork /v1/admin/* → 写 sqlite → IPC 广播 → 渲染 UI 刷新 → Sidecar 回执 dispatch_result → 服务端确认
采集流:  Sidecar 定时拉 /v1/admin/usage + 按服务端指令拉 /v1/admin/agent-files → WS usage/采集上报 → 服务端数据仓库
对账流:  Sidecar 调 list_agents → 比对服务端期望清单 → 缺的补/受管保护/非受管忽略 → 上报不一致告警
```

### 3.3 消息流（WS 8 类）

| type | 方向 | 用途 |
|------|------|------|
| register | Sidecar→服务端 | 设备注册（4.1） |
| dispatch_agent | 服务端→Sidecar | 派发 Agent（4.2） |
| dispatch_provider | 服务端→Sidecar | 派发模型 Provider（4.3） |
| sync_lock_rules | 服务端→Sidecar | 锁死规则同步（4.4） |
| usage | Sidecar→服务端 | 用量上报（4.5） |
| dispatch_result | Sidecar→服务端 | 执行回执（4.6） |
| status | Sidecar→服务端 | 状态心跳（4.7） |
| fetch_patch / install_gitbash | 服务端→Sidecar | 补丁/Git Bash 安装（4.8） |

### 3.4 事件流（热更新链路）

```
Fork 管理路由（服务端/Sidecar 侧写 sqlite）
  → 主进程补 IPC 刷新广播（新 channel data-changed，携带刷新目标）
  → 渲染进程监听广播 → invalidateQueries('/agents' | '/providers' | ...)
  → TanStack Query 重新拉取 → UI 即时刷新（不重启）
兜底：方案 A 失败 → 方案 C 强制重启兜底（需如实汇报）
```

---

## 4. 边界

| 边界 | 说明 |
|------|------|
| **采集范围** | 整个智能体工作目录（Agent 的上下文、产出内容、数据文件全部在内），不只用量计数；因 CherryStudio 是企业工作软件，公司对员工工作产生的上下文与产出拥有管理权（合规） |
| **不越权** | 采集限 Agent 工作目录（`accessible_paths` 内），**不越权读取工作目录之外的个人文件** |
| **key 归属** | key 归第三方中转侧持有；Fork 管理路由**无法控制中转侧 key 停用**，只能更新员工端 provider 的 key 字段 |
| **R2 物理隔离做不到** | 无模型网关时 key 必然物理存在于员工端 sqlite（user_provider 表）；R2 落地为「UI 不可见 + 异常停 key 兜底」，**做不到物理隔离**（已如实向老板说明并获认可） |
| **不做模型网关** | 老板否决（维护成本高、简单事情复杂化） |
| **Fork 不做的事** | 不逐版本追官方小版；按需升级（D19 触发标准）；不直写 sqlite（D20） |
| **热更新兜底** | 方案 A（IPC 广播）优先；做不出则降级方案 C（强制重启兜底），如实汇报 |

---

## 5. 关键架构决策与权衡（D1-D20 背后的为什么）

### 5.1 方向性决策（v4.0 定案）

| 决策 | 为什么 | 权衡 |
|------|--------|------|
| **D17 不用模型网关**（老板决策 A） | 网关维护成本高、引入新单点、简单事情复杂化 | 换来 R1+R2 直连 provider 派发 key + 花费监控异常停 key 重建；代价是 R2 无物理隔离 |
| **D18 跟随官方最新 + Fork 适配**（老板决策 B） | 锁定 v1.9.x 免 Fork 的假设不成立（官方 v2 已移除 agents HTTP API）；v2 provider/agent 存 sqlite，Fork 管理路由直写比 v1 简单一个数量级 | 代价是持续适配官方高频发版；靠增量 patch + 自动构建流水线 + 按需升级节奏控制 |
| **D19 按需升级触发标准** | 只追安全修复/重要功能/关键 bug，普通更新不追，保持 Fork 稳定 | 控制适配成本；触发标准待老板固化 |
| **D20 管理路由走 Service 不直写 sqlite** | 官方 drizzle+libsql 单连接 WAL 写门控，直写有锁库/损坏/事务绕过风险 | 复用 AgentService/ProviderService 公开方法，规避风险；仅官方无方法时才补且遵循同样事务模式 |

### 5.2 需求落地决策

| 决策 | 为什么 |
|------|--------|
| **D1 企业模型命名隔离** | `企_` 前缀约定隔离 + managed_entity 真正控制；员工自配自由 |
| **D2 Key 保护** | 改「UI 锁死隐藏 + 花费监控异常停 key 重建」（见 D17） |
| **D3 Agent 完整打包** | agent-package.zip（agent.json + skills/ + mcp/ + data/），开箱即用 |
| **D4 SKILLS 版本仓库** | skills-repo/{name}/{version}/，Agent 包引用 skill-a@v1.2 |
| **D5 自进化双路径** | 预设 SKILLS + 服务端数据闭环 |
| **D6 设备鉴权** | 每设备唯一 device_id + token；WS 连接需 token |
| **D7 派发幂等** | request_id + 回执 + 断线重发不重复创建 |
| **D8 设备分组** | 按组派发（设计组/客服组） |
| **D9 离线处理** | 离线指令入队，重连补发 |
| **D10 锁死规则远程化** | 机制固定 + 规则远程拉取 |
| **D11 升级免疫** | 禁用官方 feed + Fork 增量 patch + 自建更新通道（见 D18/D19/D20） |
| **D12 Git Bash 依赖可见 + 可远程装** | bundled MinGit 兜底 git 命令；完整 Git Bash（bash 终端）缺失可远程装 |
| **D13 受管 Agent 不可删除** | 受管保护语义 |
| **D14 Agent 全维度控制** | 派发/收回/禁用/升级 |
| **D15 受管删除保护统一机制** | Agent/模型/SKILLS/MCP 统一 managed 判定（旁路表） |
| **D16 Agent 包上传与版本管理** | 服务端 agents_lib |

### 5.3 质询收敛决策（技术风险化解）

| 决策 | 化解的质询 | 为什么 |
|------|-----------|--------|
| **独立管理 key** | Q-A1 员工拿到普通 key 就能调管理路由的后门 | 独立 managed_key + timing-safe bearer；Sidecar 加密存储不落明文 |
| **受管标记走旁路表** | Q7/Q-A3 加 managed 列有迁移/锁库风险 | 独立 sqlite managed_entity，Sidecar 唯一写者，不动官方 schema |
| **锁死 UI 走源码级** | Q2/Q-A4 asar 注入脆弱、注入点漂移 | 改 Fork 渲染组件（编译期固定）+ lock_rules 远程化策略参数；代价每版 rebase |
| **Fork 管理路由复用 Service** | Q-A15 直写 sqlite 破坏事务 | 复用 AgentService/ProviderService（D20） |
| **热更新自建链路** | Q-A14/Q-A19 官方无「服务端写入→UI 刷新」现成链路 | 自建 IPC 广播 → invalidateQueries；M0 必测 |
| **花费监控完整模块** | Q-A7/Q-A8 老板决策 A 无可执行细节 | 落地数据源/异常判定/停 key 流程/重建推送/看板 + 中转对接 |
| **Sidecar 独立 NSSM** | Q-A10/Q14 Sidecar 单点、CherryStudio 未启动 | 独立常驻，报 cherry_online:false，指令入队，启动后执行 |
| **周期性对账** | Q15/Q-A16 员工手动改 vs 受管不一致 | Sidecar 调 list_agents 比对期望清单，缺的补/受管保护/非受管忽略，Web 后台可见告警 |
| **Web 后台鉴权 + 审计** | Q17 Web 后台裸奔重大安全 | 管理员账号登录 + 操作审计日志 + 设备 token 可轮换 + request_id 全链路追踪 |
| **升级失败回滚** | Q-A18 失败场景 | 保留上一版备份；Fork 升级失败回滚；服务端非运行依赖，恢复后补传 |

---

## 6. 不做的事（明确排除）

1. **不做模型网关**（老板否决，D17）。
2. **不做 R2 物理隔离**（无网关做不到，已如实说明并获认可；落地为 UI 隐藏 + 停 key 兜底）。
3. **不控制中转侧 key 停用**（key 归中转侧，走中转侧对接通道或人工兑底）。
4. **不从零开发官方 CherryStudio**（跟随官方最新 + 增量 patch 适配）。
5. **不逐版本追官方小版**（D19 按需升级）。
6. **不直写官方 sqlite**（D20 走 Service 方法）。
7. **不越权读取工作目录外个人文件**（R6 采集合规边界）。

---

## 7. 里程碑（引用任务分解-v4.0.md）

| 里程碑 | 内容 | 交付判据 | 关键任务 |
|--------|------|---------|---------|
| **M0** | 求证验证 | 热更新链路通 + Windows 包可构建 + 官方最新基线 + 管理路由可写 | M0-1~M0-5 |
| **M1** | Fork 层完成 | /v1/admin/* 全路由 + 独立 key + 受管保护 + 热更新 + 锁 UI + 禁更新通道 | F-1~F-12 |
| **M2** | Sidecar 闭环 | 设备注册 + 派发 Agent/模型/SKILLS + 对账 + usage 上报 + 自愈 | S-1~S-10 |
| **M3** | 服务端 + 花费监控 | Web 后台 + 花费看板 + 异常停 key + 派发调度 | D-1~D-10 |
| **M4** | 构建发布 + 端到端 | 安装包 + 自建更新通道 + 全链路验收 | E-1~E-4 |

**关键路径**：M0-3（官方基线）→ F-1 → F-2/F-4/F-5（管理路由）→ M0-1（热更新验证）→ F-9（热更新广播）→ M1 → S-1~6 → M2 → D-6（花费监控）→ M3。

---

## 8. 老板可读摘要（≤200 字）

CherryStudio 企业受管版让公司远程统一管理员工 Windows 上的 CherryStudio：三层协作（Fork 改官方加管理路由 + Sidecar 常驻 + 服务端调度中枢），实现模型可派可收、Key 员工不可见、Agent/SKILLS 远程管理、UI 锁死、用量花费监控、升级不破坏受管。老板 8-07 定案：不用模型网关，用直连派发 key + 花费异常停 key 重建兜底；跟随官方最新 + Fork 适配；key 归第三方中转侧，停 key 走中转对接。核心风险是热更新链路（写库后 UI 即时刷新），M0 优先实测，失败降级强制重启兜底。

---

## 9. 变更记录

| 版本 | 日期 | 变更内容 | 来源 |
|------|------|---------|------|
| v1.0 | 2026-08-07 | 初始创建，基于方案 v4.0 最终版 + 任务分解 + 质询记录 + 源码校验报告 | 研发主管 daima |