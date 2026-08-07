# CherryStudio 企业受管版 · 方案 A：服务端 + Sidecar 远程闭环

> 状态: 方案待审 | 最后更新: 2026-08-06
> 负责人: 鮱澄军团
> 前置: M2 原型已验证官方 API 派发闭环（创建/更新/回收 Agent 全通）
> 目标: 把 Sidecar 从「连单机测试」升级为「连服务端多机统一管理」

---

## 一、本方案要解决什么

M2 原型证明了**单机**的 Agent 派发可行（`sidecar.py deploy --machine HOST_CHEN`）。
但那是「人肉指定一台机器」，不是真正的企业管控。本方案把架构升级为：

```
┌───────────── 管理服务端 (server/) ─────────────┐
│  · 统一管理所有员工设备                        │
│  · 集中派发/回收 Agent + SKILLS                │
│  · 接收使用数据                                │
└──────────▲──────────────────────────▲────────┘
           │ HTTPS/WS 主动连接           │
┌──────────┴──────────┐      ┌──────────┴──────────┐
│ 员工设备 A           │      │ 员工设备 N           │
│  ┌──────────────┐   │      │  ┌──────────────┐   │
│  │ CherryStudio │   │      │  │ CherryStudio │   │
│  └──────▲───────┘   │      │  └──────▲───────┘   │
│  ┌──────┴───────┐   │      │  ┌──────┴───────┐   │
│  │  Sidecar     │   │      │  │  Sidecar     │   │
│  └──────────────┘   │      │  └──────────────┘   │
└─────────────────────┘      └─────────────────────┘
```

**核心转变**：Sidecar 从「命令行工具」变成「常驻受管客户端」，主动连接服务端，服务端对多台设备统一调度。

---

## 二、架构设计

### 2.1 通信模型：Sidecar 主动连接（关键决策）

**为什么 Sidecar 主动连服务端，而不是服务端连 Sidecar？**
- 员工设备在**内网/可能 NAT 后**，服务端无法主动连进去
- Sidecar 主动外连，**绕开端口转发**（不再依赖 `chery_api_lan_23333.bat` 的 netsh portproxy）
- 服务端只需一个公网/内网可达地址，所有 Sidecar 连上来

**协议选择**：WebSocket（长连接，双向实时）
- 服务端 → Sidecar：派发指令（创建/更新/回收 Agent、同步 SKILLS）
- Sidecar → 服务端：心跳、上报状态、上报使用数据
- 比 HTTP 轮询实时、省资源

### 2.2 服务端（server/）模块划分

```
server/
├── main.py              # 入口，启动 WS 服务
├── ws_server.py         # WebSocket 服务端（设备连接管理）
├── device_registry.py   # 设备注册表（在线/离线/状态）
├── dispatch.py          # 派发调度（Agent/SKILLS 指令下发）
├── agents_lib/          # Agent 模板库（版本管理）
│   └── {agent_name}/
│       ├── agent.json   # Agent 定义
│       ├── skills/      # SKILLS 目录
│       ├── mcp/         # MCP 配置
│       └── data/        # 预设数据文件
├── skills_repo/         # SKILLS 版本仓库
│   └── {skill_name}/{version}/
├── data/                # 使用数据存储
└── config.json          # 服务端配置（端口/鉴权）
```

### 2.3 Sidecar（sidecar/）升级为常驻进程

```
sidecar/
├── sidecar.py           # 主进程（常驻，连接服务端）
├── lib/
│   ├── cherry_client.py # 已有：官方 API 客户端
│   ├── ws_client.py     # 新增：WebSocket 客户端
│   └── sync.py          # 新增：派发/回收执行器
├── config/
│   └── sidecar.json     # 本机配置（服务端地址/设备ID/本地端口）
└── agents/              # 本地 Agent 数据暂存
```

**Sidecar 常驻职责**：
1. 开机自启，连接服务端 WS
2. 收到派发指令 → 调 `cherry_client` 执行（创建/更新/回收 Agent）
3. 收到 SKILLS 同步指令 → 写本地数据目录
4. 定时上报状态 + 使用数据
5. 断线自动重连

---

## 三、通信协议（WS 消息格式）

### 3.1 设备注册（连接建立时）
```json
// Sidecar → 服务端
{
  "type": "register",
  "device_id": "HOST_CHEN-001",
  "hostname": "HOST_CHEN",
  "os": "windows",
  "cherry_version": "2.0.0",
  "api_key": "***"  // 本机 CherryStudio 的 key，供服务端代管
}
```

### 3.2 派发指令（服务端 → Sidecar）
```json
// 创建/更新 Agent
{
  "type": "dispatch_agent",
  "action": "create",          // create | update | delete | disable
  "agent": {
    "name": "企_客服助手",
    "model": "deepseek:deepseek-v4-flash",
    "instructions": "...",
    "accessible_paths": ["D:\\...\\Agents\\deployed"],
    "configuration": {"permission_mode": "bypassPermissions", "max_turns": 100}
  },
  "skills": ["skill-a@v1.2", "skill-b@v2.0"],  // 附带 SKILLS
  "request_id": "req-001"
}
```

### 3.3 执行回执（Sidecar → 服务端）
```json
{
  "type": "dispatch_result",
  "request_id": "req-001",
  "success": true,
  "agent_id": "agent_xxx",
  "error": null
}
```

### 3.4 状态上报（Sidecar → 服务端，定时）
```json
{
  "type": "status",
  "device_id": "HOST_CHEN-001",
  "online": true,
  "agents": ["企_客服助手", "企_设计助手"],
  "cherry_healthy": true,
  "last_sync": "2026-08-06T10:00:00Z"
}
```

### 3.5 使用数据上报（Sidecar → 服务端，定时/事件）
```json
{
  "type": "usage",
  "device_id": "HOST_CHEN-001",
  "period": "2026-08-06T09:00:00Z/2026-08-06T10:00:00Z",
  "agent_usage": {
    "企_客服助手": {"sessions": 12, "messages": 45},
    "企_设计助手": {"sessions": 3, "messages": 8}
  },
  "errors": ["..."]
}
```

---

## 四、核心流程

### 4.1 设备上线
```
1. Sidecar 开机自启
2. 连接服务端 WS
3. 发送 register（设备信息 + 本机 key）
4. 服务端登记设备 → 返回「该设备应有哪些 Agent/SKILLS」清单
5. Sidecar 比对本地 → 差异同步（缺的创建，多的回收）
6. 进入常驻监听
```

### 4.2 派发新 Agent（管理员操作）
```
1. 管理员在服务端「派发 Agent 企_客服助手 给 设备组A」
2. 服务端向组内每台 Sidecar 发 dispatch_agent
3. Sidecar 调 cherry_client.create_agent() 创建
4. 回执 dispatch_result
5. 服务端更新设备状态
```

### 4.3 回收 Agent
```
1. 管理员「回收 企_客服助手」
2. 服务端发 dispatch_agent action=delete
3. Sidecar 调 delete_agent() 删除
4. 回执
```

### 4.4 数据采集闭环
```
1. Sidecar 定时采集使用数据 → 上报
2. 服务端存储 → 分析
3. 分析结果 → 驱动新 Agent/SKILLS 开发
4. 派发回设备
```

---

## 五、关键设计决策

### D-A1 设备鉴权
- 每台设备有唯一 `device_id` + 注册时校验
- 服务端 WS 连接需 token（防未授权设备连入）
- 本机 CherryStudio 的 key 由 Sidecar 持有，**不上传明文**（或加密传输）

### D-A2 派发幂等性
- 每个指令带 `request_id`，Sidecar 执行后回执
- 服务端记录已派发状态，**重复派发不重复创建**（同名 Agent 先查再建）
- 断线重连后，服务端重发未确认指令

### D-A3 设备分组
- 支持按组派发（如「设计组」「客服组」）
- 组内设备统一收指令，减少逐个操作

### D-A4 离线处理
- 设备离线时，指令进入队列
- 设备重连后，服务端补发积压指令
- 保证「派发不丢」

### D-A5 与 R1 模型管控的关系
- 本方案（A）聚焦 **Agent/SKILLS 派发**（官方 API 已支持）
- **模型管控（R1）官方 API 不支持**（`/v1/providers` 404），需后续单独方案（Fork 或直写 sqlite）
- 本方案预留 `dispatch_provider` 消息类型，R1 落地后复用同一通道

---

## 六、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 服务端框架 | Python FastAPI + uvicorn | 轻量、异步 WS 支持好、与 Sidecar 同语言 |
| WS 库 | `websockets`（服务端）/ `websocket-client`（Sidecar） | 成熟稳定 |
| 数据存储 | SQLite（起步）→ PostgreSQL（规模化） | 起步简单，可扩展 |
| Sidecar 常驻 | Python + systemd（Linux）/ NSSM（Windows） | 开机自启、崩溃自愈 |
| 设备注册表 | 内存 + SQLite 持久化 | 在线状态实时，历史持久 |

---

## 七、交付里程碑（本方案 A）

```
A0 服务端骨架 → WS 服务 + 设备注册 + 心跳
A1 派发闭环   → 服务端派发 Agent 到单台设备（复用 M2 验证）
A2 多机管理   → 设备分组 + 批量派发 + 离线队列
A3 数据采集   → Sidecar 上报使用数据 + 服务端存储
A4 完整闭环   → 派发 + 回收 + 数据 + 状态看板
```

---

## 八、待求证 / 阻塞项

- [x] **A0 环境**：服务端部署在本机（老板已定）
- [x] **A0 端口**：服务端 WS 端口 = **2334**（老板已定）
- [x] **A1 鉴权**：简单 token（老板已定）
- [x] **A3 数据范围**：全量数据，但**按需采集**（不主动采集，调用数据回传分析时才采集；公司内部工作完全合规）
- [ ] **R1 模型管控**：本方案不含，需单独方案（Fork 或直写 sqlite）

---

## 九、与总方案的关系

本方案是总方案（v1.0）中 **M2 里程碑的完整化**，聚焦「服务端 + Sidecar 远程闭环」。
- 总方案 M0（构建验证）、M1（Fork 锁死）、M3（模型管控）、M4（数据闭环）**不在本方案范围**
- 本方案完成后，Sidecar 具备「多机统一派发 Agent/SKILLS」能力，为后续 M3/M4 打基础

---

## 十、已确认的决策点（老板拍板）

1. **服务端部署位置**：本机 ✅
2. **服务端 WS 端口**：**2334** ✅
3. **设备鉴权强度**：简单 token ✅
4. **数据采集范围**：全量数据，但**按需采集**（不主动采集，调用数据回传分析时才采集；公司内部工作完全合规）✅
5. **实施节奏**：先跑通再拓展；先出方案再出开发文档 ✅
