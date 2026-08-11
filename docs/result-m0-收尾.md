# M0 收尾实测结果（JJC-20260810-003）

_记录时间: 2026-08-11_
_状态: 部分完成 — 代码/出包/路由验证已通过；完整数据验证并入 M2 Sidecar 自动验证（老板 2026-08-11 拍板）_

## 一、范围与决策

- **任务**: 扩展 admin 路由一次实测 M0-1②③ + M0-4 + M0-5
- **老板决策 (2026-08-11)**: 完整数据验证（M0-1②③/M0-4/M0-5 的写库+UI刷新断言）**并入 M2 Sidecar 自动验证**，不再手动命令行验证（PS/curl/浏览器受限，成本高易踩坑）
- **理由**: M0-1① Agent 热更新已实测通过，核心链路（管理路由写库 → IPC 广播 → UI 刷新）已证明成立；②③/4/5 是同一条链路的不同路由，风险低，应由 Sidecar 自动断言而非人工

## 二、已完成并验证 ✅

### M0-1① Agent 热更新（result-m0-1.md，之前已通过）
- `PUT /v1/admin/agents/:id` 改名 → UI 即时刷新，无需重启
- 链路: admin route → AgentService.updateAgent → notifyDataApiDataChange → useDataChange → UI 刷新

### admin 路由代码扩展（commit 0ef01567f）
- 新增 6 路由，覆盖 M0-1②③ + M0-4 + M0-5:
  - `POST /v1/admin/agents` (M0-4 创建 Agent)
  - `GET /v1/admin/providers` (M0-1②/M0-4)
  - `POST /v1/admin/providers` (M0-1②/M0-4)
  - `PUT /v1/admin/providers/:id` (M0-1②/M0-4)
  - `PUT /v1/admin/providers/:id/api-keys` (M0-1③ 推 key)
  - `GET /v1/admin/usage` (M0-5 读用量)
- 铁律落实: 全走 Service（不直写 sqlite，D20）、写库后 notifyDataApiDataChange 广播（复用官方 data-api:data-changed）、继承 Bearer 鉴权
- typecheck 全绿，CI run 31391280689 success（head 0ef01567f），Windows NSIS 包已出
- **17 号机已安装该最新包**

### admin 路由响应验证 ✅（2026-08-11）
- 浏览器访问 `http://127.0.0.1:23333/v1/admin/providers` → 返回 `{"error":"Unauthorized: missing credentials"}`
- **结论**: admin 路由**存在且正常响应**（非 404/断连），缺凭据是因为浏览器无法带 `Authorization: Bearer` 头
- 17/188 均返回该 JSON → 路由挂载正确

## 三、关键技术教训

### PowerShell 测试的坑
- `$HOST` 是 PowerShell **保留只读变量**，不能赋值（用 `$API`/`$BASE`）
- `curl` 在 PowerShell 是 `Invoke-WebRequest` 别名，不是真 curl；Windows 可能无 `curl.exe`
- `Invoke-RestMethod` 对 4xx 响应抛异常，错误被包装成"连接被意外关闭"（误导）→ 用 `-SkipHttpErrorCheck` 拿真实状态码
- **浏览器无法带 `Authorization: Bearer` 头** → 浏览器测不到需鉴权的 admin 路由

### 17 号机网络
- 服务器 ping 17 号机通（0.5ms），但 **TCP 23333 从外部连不通**（防火墙/网关只监听回环）
- **这符合设计**: 员工机 API Gateway 本就该只对本机开放（服务端永不直连员工机 23333，安全设计，方案 v4.0）
- 完整验证必须走 Sidecar 本机转发（WS 2334）

## 四、并入 M2 的验证清单（Sidecar 自动断言）

M2 Sidecar 装上后，自动跑以下断言（替代人工命令）:
1. **M0-1②** `POST /v1/admin/providers` + `GET /v1/admin/providers` → 写库 + 模型下拉 UI 即时刷新（不重启）
2. **M0-1③** `PUT /v1/admin/providers/:id/api-keys` → 受管 provider 免重启生效
3. **M0-4** `POST /v1/admin/agents` → 创建 Agent + UI 即时出现
4. **M0-5** `GET /v1/admin/usage` → 读出模型+token 用量，字段完整

## 五、看板状态

- JJC-20260810-003 曾被调度器 autoRollback 挂起为 Blocked（调度器 bug，派子任务未及时 advance-state 触发），已 resume
- 结论: M0 收尾代码部分完成，数据验证并入 M2；任务按并入 M2 收口