# M0-1 热更新链路实测 · 执行结果（V-M0-1）

> **执行者**：研发主管 daima（代码）+ 老板（17 号机 HOST_17 手动实测）｜**日期**：2026-08-10
> **状态**：① Agent 热更新 ✅ 通过（老板硬要求核心项）｜②③ Provider/Key 热更新 ⏳ 未测（属 M0-4/F 系列，不在 M0-1 代码范围）
> **依据**：plan-m0.md §一（M0-1）+ 方案-企业受管版-v4.0 §13.1 V-M0-1 + 源码校验报告-v2.0.1
> **测试机**：**HOST_17**（非 plan 原定的 188——188 已装官方版无 admin 路由，改在老板手动装的 Fork 版 17 号机实测）

---

## 〇、关键结论先行

**M0-1 核心链路验证通过**：`PUT /v1/admin/agents/{id}` 写 sqlite → IPC 广播 → UI **不重启即时刷新** 全链路成立。

**⚠️ 重要技术教训（已记入，Sidecar 调 admin 路由必读）**：
- admin 路由鉴权**只认 `Authorization: Bearer <key>` 头**，**不认 `x-api-key` 头**（后者返回 403 Forbidden）。
- 官方路由（如 `/v1/models`）两种头都认，但 admin 路由因是独立 Elysia 实例挂载在 scoped guard 后，`x-api-key` 头未正确传入 `authorizeApiRequest`，仅 `@elysia/bearer` 解析的 `bearer` 生效。
- **后续 Sidecar 调所有 `/v1/admin/*` 路由必须用 `Authorization: Bearer` 头**（F-3 独立 managed_key 设计时同样约束）。

---

## 一、前置条件核对

| 前置条件（plan §1.1） | 状态 |
|----------------------|------|
| 本地 v2.0.1 基线源码 | ✅（后已拉至 12498d6，M0-3 完成） |
| M0-3 建 managed 分支 | ✅（managed/main，HEAD=a4d335dc，含 M0-1 代码） |
| 测试机可达 | ✅ 17 号机（HOST_17）在线，CherryStudio Fork 版运行中 |
| 测试机装好待测 Fork 构建 | ✅ 老板手动安装 CI 产物 `Cherry-Studio-2.0.3-x64-setup.exe`（run 31363847257，SHA a4d335dc） |

---

## 二、M0-1 代码交付（已提交 push）

改动 3 文件（commit `2f0dda5f` + `a4d335dc`，分支 managed/main）：

| 文件 | 类型 | 内容 |
|------|------|------|
| `src/main/features/apiGateway/adminRoutes.ts` | **纯新增**（85行） | `PUT /v1/admin/agents/:id`，zod 校验（name/instructions 至少一项），内部调 `AgentService.updateAgent`（走 Service 不直写 sqlite，D20），成功后 `notifyDataApiDataChange([{endpoint:'/agents',kind:'projection'}])` 广播 |
| `src/main/features/apiGateway/app.ts` | 改官方（+4行） | import adminRoutes + `v1Routes` 链尾 `.use(adminRoutes)`，继承 scoped bearer 鉴权 |
| `src/renderer/hooks/agent/useAgent.ts` | 改官方（+3行） | `useAgents()` 内新增 `useDataChange('/agents', ()=>refetch())` 订阅广播 |

**审计修正落地**：plan 写的"新 channel data-changed + invalidateQueries"与实际源码不符，实为**复用官方 `notifyDataApiDataChange()` → `data-api:data-changed` channel → 渲染层 `useDataChange`**（非新增 channel/API，严格复用官方机制）。typecheck 全绿（exit 0）。

---

## 三、17 号机实测过程与证据（V-M0-1 ① Agent 热更新）

### 3.1 实测前置确认（17 号机 PowerShell）

| 确认项 | 结果 |
|--------|------|
| Fork 包确认 | `D:\Cherry Studio\resources\app.asar` 202400592 字节；OpenAPI（`http://127.0.0.1:23333/openapi`）含 `PUT /v1/admin/agents/{id}`，description="...managed admin route, M0-1..." → **确认是 Fork 包** |
| key 有效性 | `GET /v1/models` 带 key → **200** 模型列表 ✅ |
| m0test agent id | `Data\Agents\` 按创建时间倒序最新目录 = `6c379e6c-a8a7-41e5-9b15-dd16e2ee707a`（19:27 创建，即 m0test）|

### 3.2 核心实测命令与结果

**命令**（17 号机 PowerShell）：
```powershell
$id = "6c379e6c-a8a7-41e5-9b15-dd16e2ee707a"
$key = "<真实 key>"
$body = @{ name = "m0test-bearer测试" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:23333/v1/admin/agents/$id" -Method Put `
  -Headers @{ "Authorization" = "Bearer $key"; "Content-Type" = "application/json" } -Body $body
```

**返回（200 成功）**：
```
id                                   name             instructions                 updatedAt
--                                   ----             ------------                 ---------
6c379e6c-a8a7-41e5-9b15-dd16e2ee707a m0test-bearer测试 You are a helpful assistant. 2026-08-10T12:34:41.992Z
```
→ name 从 `m0test` 改为 `m0test-bearer测试`，updatedAt 更新，**写库成功**。

### 3.3 鉴权头对比（定位 403 根因）

| 请求头 | 结果 |
|--------|------|
| `x-api-key: <key>` | ❌ 403 Forbidden |
| `Authorization: Bearer <key>` | ✅ 200 成功 |

**结论**：admin 路由鉴权只认 Bearer 头（见〇关键结论）。

### 3.4 UI 即时刷新验证（决定性证据）

**PUT 成功后，老板在 CherryStudio UI 左侧确认：m0test 名字即时变为 `m0test-bearer测试`，未重启程序。**

→ **IPC 广播 → 渲染层 useDataChange 刷新 链路成立。**

---

## 四、V-M0-1 验收判定

| 判据 | 通过标准 | 结果 |
|------|---------|------|
| **① Agent 热更新** | 改 Agent 名后 UI **不重启**即显示新值 | ✅ **通过**（m0test 即时改名，实测证据见 §3.4） |
| ② Provider 热更新 | 派发 provider 后模型下拉**即时可选** | ⏳ 未测（M0-1 代码范围仅 ①；②属 M0-4/F 系列） |
| ③ Key 热更新 | replaceApiKeys 后受管 provider **立即生效** | ⏳ 未测（同上，属 M0-4/F 系列） |

**V-M0-1 总判据（plan §1.3：①+②+③ 全绿才进 M1）**：
- **① ✅ 绿**（老板硬要求核心项，证明"管理路由写库→IPC 广播→UI 即时刷新"链路成立）
- **②③ ⏳ 未测**——**非 M0-1 代码范围**（plan §1.2 明确 M0-1 最小改动只做"改 Agent"一个动作，②③ 属 M0-4/F 系列同批管理路由）

> **说明**：M0-1 的核心求证目标是**证明链路成立**，① 已充分证明。②③ 是同一链路在 provider/key 数据上的扩展验证，归入 M0-4（管理路由对接，plan §四）统一实测，避免在 M0-1 重复铺代码。

---

## 五、风险与兜底执行情况

| 项 | 状态 |
|----|------|
| plan §1.5 风险（IPC 广播做不出/UI 不刷新） | ✅ **未发生**——① 链路实测成功 |
| 方案 C（强制重启兜底） | 无需启用（链路本通） |

---

## 六、M0-1 遗留与下一步

1. **②③ 待 M0-4 实测**：provider/key 热更新，复用 M0-1 的 IPC 广播 + admin 路由骨架扩展。
2. **鉴权头约束写入设计**：F-3（独立 managed_key）设计时明确**所有 `/v1/admin/*` 走 Bearer 头**；Sidecar 客户端（M2）统一用 `Authorization: Bearer`。
3. **测试机切换记录**：本项实测用 17 号机（老板手动装 Fork 版）替代 plan 原定 188（188 装的是官方版无 admin 路由）。后续 M0-4/M0-5 实测机需统一确认（188 需重装 Fork 版，或沿用 17 号机）。
4. **M0-1 看板推进**：JJC-20260809-001 状态推进（Doing 阶段项完成）。