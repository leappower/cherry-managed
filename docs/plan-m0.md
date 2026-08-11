# M0 求证阶段 · 可执行实施计划（cherry-managed）

> **文档版本**：v1.0 | 日期：2026-08-09 | 状态：待研发主管执行
> **产出者**：军师 guihua
> **依据**：方案-企业受管版-v4.0.md（第十三节 V-M0-1~5）+ 任务分解-v4.0.md（A 节 M0-1~5）+ CDD/SDD v1.0 + 源码校验报告-v2.0.1.md
> **性质**：不是重写方案，是把 M0 的 5 项求证任务拆成"研发主管可直接照着执行"的步骤 + 验收判据 + 依赖 + 风险兜底 + 顺序。
> **关键约束**：热更新、锁死 UI 为老板硬要求；**V-M0-1（热更新）不达标不进入 M1**（方案 13.1）。全部基于方案 v4.0 + 源码校验报告技术事实，不自行发明。

---

## 〇、M0 前置基线（已核实的源码事实，供研发直接引用）

以下事实已在本地 `cherry-src` 源码核清，作为本计划全部步骤的技术地基（对应源码校验报告 §二）：

| # | 事实 | 源码位置 | 对本计划的意义 |
|---|------|---------|--------------|
| B1 | 数据存 sqlite：`{userData}/Data/cherrystudio.sqlite` | `src/main/services/pathRegistry.ts` / `dataReset.ts` | 管理路由写库目标 |
| B2 | 官方 API Gateway 无任何 `/v1/admin/*` 管理路由 | `src/main/features/apiGateway/app.ts` `v1Routes` | M0-4 必须新增 |
| B3 | API Gateway 挂载点 = `buildApp({host='127.0.0.1', port=23333})` | `src/main/features/apiGateway/app.ts:71` | 管理路由加在这里，端口 23333 |
| B4 | 鉴权链 = `@elysia/bearer` + `authorizeApiRequest(x-api-key, bearer)`，timing-safe | `app.ts` / `middleware/auth` | M0-4 复用该鉴权骨架 |
| B5 | AgentService 在 `src/main/data/services/AgentService.ts` | （任务分解 F-4 需此路径） | M0-4 复用 CRUD 方法 |
| B6 | ProviderService 在 `src/main/data/services/ProviderService.ts` | （任务分解 F-5 需此路径） | M0-4 复用 CRUD 方法 |
| B7 | `createAgentWithId` 在 `src/main/ai/agents/createAgent.ts` + `AgentService.ts` | 校验报告假设成立 | M0-4 建 Agent 用 |
| B8 | `ai_usage_record` 表定义 `src/main/data/db/schemas/aiUsageRecord.ts`：字段含 providerId/providerName/modelId/modelName/inputTokens/outputTokens/totalTokens/sourceType/sourceId/sourceName/requestId/recordKind | SDD 4.2 字段全部存在 | M0-5 读取字段 |
| B9 | `v1Routes` 已挂 messagesRoutes/chatRoutes/responsesRoutes/modelsRoutes/knowledgeRoutes | `app.ts` | M0-4 在链尾追加 adminRoutes |
| B10 | 渲染层用 TanStack Query（`useDataApi`），数据刷新靠 invalidateQueries | 方案 §5.1 | M0-1 广播后刷新 |
| B11 | Windows 打包：`win.target=[nsis,portable]`；artifactName `${productName}-${version}-${arch}-setup.${ext}`；脚本 `build:win:x64` = `electron-builder --win --x64` | `electron-builder.yml` / `package.json` | M0-2 构建入口 |
| B12 | `publish: {provider:generic, url:https://releases.cherry-ai.com}` | `electron-builder.yml:147` | M0-2 验证更新 feed（正式改到 F-11/E-2） |
| B13 | 已有 `.github/workflows/ci.yml` 等官方流水线先例 | `.github/workflows/` | M0-2 新增专用 workflow 参考 |
| B14 | 本地基线 `f39b17d04` = v2.0.1（校验报告基线），**落后 origin/main 21 commits** | git log | M0-3 需拉最新 |

### 本地仓库与测试机现状

- **本地 cherry-src 主仓库**：`/home/chee/.openclaw/workspace-main/cherry-src`
  - HEAD = `f39b17d04`（v2.0.1），`main...origin/main` **落后 21 commits**
  - origin remote = `https://github.com/CherryHQ/cherry-studio.git`
  - 任务分解 M0-3 引用的 origin/main `b70d89f`（Firecrawl URL fetch）是计划撰写时的 main 顶端之一；`git ls-remote` 显示当前 main 顶端已推进到 `12498d6`，且历史上可看 `a9b913dbf chore: release v2.0.2`。**→ M0-3 一律以 `git ls-remote origin main` 实时取最新为准，不锁死旧 commit 号。**
- **测试机**：
  - `HOST_CHEN` `HOST_188:23333` —— 跑非官方补丁版（M0-1/M0-4/M0-5 实测用）
  - `HOST_LIANG` `HOST_69:23333` —— 备用/对账验证
  - 看板生产：`http://HOST_181:7891`；服务端端口 2334（方案）

---

## 一、M0-1 热更新链路实测（老板硬要求，V-M0-1）

> **目标**：证明"Fork 管理路由写 sqlite → IPC 广播 → 渲染 invalidateQueries → UI 即时刷新"链路成立，为 F-9 铺路。
> **验收标准**：方案 13.1 V-M0-1 与 SDD §6.2 三项：①改 Agent 名/提示词→UI 即时更新；②派发 provider→模型下拉即时可选；③停 key 重建推新 key→受管 provider 生效，员工端无需重启。

### 1.1 前置条件
- [x] 本地有 v2.0.1 基线源码（`/home/chee/.openclaw/workspace-main/cherry-src`，HEAD=f39b17d04）
- [ ] M0-3 已完成：拉到最新 origin/main 并建 managed 分支（本任务基于 M0-3 的基线做最小改动，避免重复 rebase）
- [ ] HOST_CHEN HOST_188:23333 可达（`curl http://HOST_188:23333/health` 通）
- [ ] 在测试机装好待测 Fork 构建（先本机 `pnpm build:win:x64` 或 M0-2 产物）

### 1.2 具体执行动作（最小改动验证链路，不改全量功能）
1. **在 `v1Routes` 链尾追加一个最小 adminRoutes 插件**（`src/main/features/apiGateway/` 下新建 `adminRoutes.ts`），先只做"改 Agent"这一个动作的最小验证：
   - 在 `app.ts` 的 `v1Routes` 末尾 `.use(adminRoutes)`（B9）。
   - 路由：`PUT /v1/admin/agents/:id`，body 改 name/instructions，内部调用 `AgentService.updateAgent(id, patch)`（B5/B7，**走 Service 不直写 sqlite**，D20）。
   - 临时复用现有 bearer 鉴权（正式独立 managed_key 属 F-3，M0 只验证链路）。
2. **主进程补 IPC 刷新广播**：在管理路由写库成功后，向渲染进程发新 channel 广播（如 `data-changed`，携带刷新目标 `/agents`）。参考 `src/preload/preload.ts` / `ipc.ts` 的现有桥接方式新增暴露。
3. **渲染进程监听广播 → invalidateQueries**：在渲染主窗口监听 `data-changed`，对相应 query key 调 `invalidateQueries('/agents')`（B10，TanStack Query `useDataApi` 机制）。
4. **本地构建**：`pnpm build:win:x64`（B11），产物安装到 HOST_CHEN。
5. **实测三项（按 SDD §6.2）**：
   - ① 管理路由改 Agent 名/提示词 → 观察 188 测试机 UI 是否**不重启即时更新**；
   - ② 管理路由派发一个 provider → 模型下拉是否**即时可选**；
   - ③ 管理路由 replaceApiKeys 推新 key → 受管 provider 是否**立即生效**（无需重启）。

### 1.3 验收判据（V-M0-1）
| 判据 | 通过标准 | 验证方式 |
|------|---------|---------|
| ① Agent 热更新 | 改 Agent 名/提示词后，UI **不重启**即显示新值 | 实测 188 测试机，品控官 shencha 复核 |
| ② Provider 热更新 | 派发 provider 后模型下拉**即时可选** | 实测 |
| ③ Key 热更新 | replaceApiKeys 后受管 provider **立即生效**，无需重启 | 实测 |
| **V-M0-1 总判据** | ①+②+③ 全绿 | 全绿 → 进入 M1；**任一红 → 按 5.3 降级方案 C 强制重启兜底并重评** |

### 1.4 依赖
- 依赖：M0-3（基线）、M0-2（若用 CI 产物）或本机 `build:win:x64`
- 被依赖：F-9（热更新广播落地）、整个 M1 的 V-M1-4、M0-4（同批管理路由）
- **关键路径起点**：M0-3 → (M0-1) → F-9 → M1

### 1.5 风险与兜底
- **风险（🔴 高，老板硬要求）**：IPC 广播链路实测做不出 / UI 不刷新。
- **兜底（方案 5.3 方案 C）**：管理路由写完 sqlite 后触发 CherryStudio 主进程**强制重启**（或提示员工重启）兜底刷新。V-M0-1 红 → 如实上报，**降级方案 C 重评**；按方案 13.1，V-M0-1 红时阻塞 M1 直至重评结论。

---

## 二、M0-2 Windows 补丁包构建（V-M0-2）

> **目标**：证明 GitHub Actions `windows-latest` runner 能出 NSIS 安装包，消除"本机 Linux 无 Wine/Windows 打包链"的阻塞。
> **验收标准**：方案 13.1 V-M0-2 —— CI 跑 electron-builder 产出 NSIS 安装包，可安装启动。

### 2.1 前置条件
- [ ] cherry-src 有可构建基线（M0-3 的 managed 分支或当前 main）
- [ ] GitHub 仓库可达，Actions runner 可用
- [ ] `package.json` / `electron-builder.yml` 的 `build:win:x64` 脚本可用（B11 已确认存在）

### 2.2 具体执行动作
1. **新建专用 workflow**：在 `.github/workflows/` 新增 `fork-win-build.yml`（参考已有 `ci.yml` B13，但不改动官方 ci.yml）。
2. **job 配置（windows-latest）**：
   - `runs-on: windows-latest`
   - 步骤：`actions/checkout` → 装 `pnpm`（官方用 pnpm，见 package.json scripts）→ `pnpm install` → `pnpm build:win:x64`（B11）。
3. **产物**：`win.target=[nsis,portable]`（B11），NSIS 包 artifactName `${productName}-${version}-${arch}-setup.${ext}`。上传 artifact 供下载。
4. **本机验证**：把 CI 产出的 NSIS 包传到 HOST_CHEN 安装，确认能启动（V-M0-2 通过条件）。
5. **（预验证，不阻塞）**：`electron-builder.yml:147` publish generic `https://releases.cherry-ai.com` 现指向官方；本步只验证"能出包"，正式改 feed 属 F-11/E-2（不在 M0）。

### 2.3 验收判据（V-M0-2）
| 判据 | 通过标准 | 验证方式 |
|------|---------|---------|
| CI 出包 | windows-latest 跑 `build:win:x64` 无报错 | Actions 运行记录绿 |
| NSIS 产物 | 产出 `${productName}-${version}-x64-setup.exe` | artifact 下载可见 |
| 可安装启动 | NSIS 包在 HOST_CHEN 安装并启动成功 | 实测 188 测试机 |

### 2.4 依赖
- 依赖：M0-3（基线，构建用）
- 被依赖：S-10（NSSM）、E-1（构建流水线正式化）、E-3（Sidecar 打包）、M2/M4
- 与 M0-1 并行可做（构建不依赖热更新验证）

### 2.5 风险与兜底
- **风险（🟡 中）**：windows-latest runner 环境差异（Node/pnpm 版本）、electron 下载慢/镜像。
- **兜底**：`electron-builder.yml:150` 已有 `electronDownload.mirror: https://npmmirror.com/mirrors/electron/`（B12 旁证），网络问题优先走镜像；本机 Linux 侧无法直接出 Windows 包，若 CI 长期不可用，退化为"手动在任一 Windows 机执行 `pnpm build:win:x64`"（测试机 188/69 可代跑）。

---

## 三、M0-3 拉官方最新基线建 Fork 分支（V-M0-3）

> **目标**：把本地 cherry-src 从落后的 v2.0.1（f39b17d，落后 21 commits）拉到最新 origin/main，并建 managed 分支，供 M0-1/M0-4/M0-5 及 F-1 用。
> **验收标准**：方案 13.1 V-M0-3 —— cherry-src 拉 origin/main，建 managed 分支，分支存在、rebase 干净无冲突。

### 3.1 前置条件
- [ ] 网络可达 GitHub（`git ls-remote origin main` 通）
- [ ] 本地有 clean 工作区（未提交改动不阻塞，但建议 stash/独立 worktree）

### 3.2 具体执行动作
1. **确认最新基线**（不锁死旧 commit 号）：
   ```bash
   cd /home/chee/.openclaw/workspace-main/cherry-src
   git fetch origin
   git ls-remote origin main        # 取实时最新 SHA（当前 ~12498d6，会继续推进）
   ```
2. **确认本地落后量**：`git log --oneline HEAD..origin/main | wc -l`（现状 21 commits，含 v2.0.2 release `a9b913dbf`）。
3. **拉最新**：`git pull --ff-only origin main`（或 `git merge origin/main`）。
4. **建 managed 分支**（Fork 改动基线，方案 D18 分支策略 `official/main` 镜像 + `managed/xxx`）：
   ```bash
   git checkout -b managed/main origin/main     # 以最新 origin/main 为 Fork 基线
   git branch -a                                 # 验证 managed/main 存在
   ```
5. **rebase 干净性验证**：`git rebase origin/main`（当前若从干净基线 checkout 应无冲突）；若已有改动需 rebase，确认无冲突。

### 3.3 验收判据（V-M0-3）
| 判据 | 通过标准 | 验证方式 |
|------|---------|---------|
| 已拉最新 | `HEAD` = origin/main 最新 SHA（本地非落后） | `git status` / `git log` |
| managed 分支存在 | `managed/main`（或约定名）分支已建 | `git branch -a` |
| rebase 干净 | 从最新基线 checkout，无冲突 | `git rebase origin/main` 通过 |

### 3.4 依赖
- 依赖：网络
- 被依赖：**全部 M0 其余任务**（M0-1/M0-2/M0-4/M0-5 的基线）+ F-1（Fork 分支正式化）
- **建议最先做（本机可做，无需测试机）**

### 3.5 风险与兜底
- **风险（🟡 中）**：网络拉取失败；官方 main 高频推进导致基线漂移。
- **兜底**：分支名统一约定 `official/main`（官方镜像，只跟随官方）+ `managed/xxx`（Fork 改动），见方案 D18；拉取失败重试镜像/备用网络。**注意**：本步拉的 `f39b17d→最新` 会使源码校验报告的 v2.0.1 行号/组件路径漂移，**M0-1/M0-4 实施时须按最新基线重新核对注入点（方案 §6.2 明确要求），不直接套用 v2.0.1 行号。**

---

## 四、M0-4 管理路由对接验证（V-M0-4）

> **目标**：证明在官方 `v1Routes` 链追加 `adminRoutes`，复用 AgentService/ProviderService 方法写 sqlite，事务/校验不破坏、UI 刷新。
> **验收标准**：方案 13.1 V-M0-4 —— 调 `/v1/admin/agents POST` 创建成功，事务/校验不破坏，UI 刷新。

### 4.1 前置条件
- [ ] M0-1 已完成（管理路由最小骨架 + IPC 广播已在 M0-1 打通）
- [ ] M0-3 已完成（最新基线）
- [ ] HOST_CHEN HOST_188:23333 可达

### 4.2 具体执行动作
1. **在 `v1Routes` 链尾追加 adminRoutes 插件**（`src/main/features/apiGateway/adminRoutes.ts`，`app.ts` `.use(adminRoutes)`，B9）。复用官方 `@elysia/bearer` + `authorizeApiRequest` 鉴权骨架（B4）。
2. **实现 Agent 管理路由**（对接 B5/B7 AgentService）：
   - `POST /v1/admin/agents` → `AgentService.createAgentWithId(...)`，body 含 name/type/model/instructions/configuration/tools/skills（方案 §3.1）。
   - 创建成功后**复用 M0-1 的 IPC 广播** → UI 刷新。
3. **实现 Provider 管理路由**（对接 B6 ProviderService）：
   - `POST /v1/admin/providers` → `ProviderService.create(...)`；`batchUpsert`、`addApiKey`/`replaceApiKeys` 也验证可用（方案 §3.2）。
4. **写库一致性验证**：
   - 创建后 `sqlite` 表 `agent`/`user_provider` 出现新记录；
   - **校验官方事务/门控不破坏**：连续多次 create/update，验证 WAL 无锁库、无校验绕过、无损坏（源码校验报告 §2.2 写门控风险，走 Service 规避 D20）。
5. **实测**：188 测试机跑该 Fork，调 `POST /v1/admin/agents` 创建，观察 Agent 出现在 UI 且即时刷新。

### 4.3 验收判据（V-M0-4）
| 判据 | 通过标准 | 验证方式 |
|------|---------|---------|
| 创建成功 | POST /v1/admin/agents 返回 200 + id | curl 实测 |
| 事务/校验不破坏 | 连续 create/update 无锁库/校验绕过/损坏 | 日志 + sqlite 完整性检查 |
| UI 刷新 | 新 Agent 出现且即时刷新 | 实测 188 测试机（复用 M0-1） |
| 方法复用 | 走 AgentService/ProviderService，不直写 sqlite | 代码审查（D20） |

### 4.4 依赖
- 依赖：M0-1（路由骨架 + IPC 广播）、M0-3（基线）
- 被依赖：F-2/F-4/F-5（正式管理路由）、M0-5 同批路由、M1 V-M1-1

### 4.5 风险与兜底
- **风险（🟡 中）**：官方 Service 方法签名随最新基线漂移（B5/B6 路径可能变）；写库破坏官方事务。
- **兜底**：一律走 Service（D20），不直写；方法签名按最新基线重新核对；写库风险靠"连续压测 + sqlite 完整性检查"兜底，出问题回滚到 Service 直调官方方法路径。

---

## 五、M0-5 ai_usage_record 读取（V-M0-5）

> **目标**：证明管理路由能读出员工端 `ai_usage_record` 表（模型+token 用量），为 S-6 采集 / D-6 花费监控铺路。
> **验收标准**：方案 13.1 V-M0-5 —— 调 `/v1/admin/usage` 读出模型+token 用量，字段完整。

### 5.1 前置条件
- [ ] M0-3 已完成（最新基线）
- [ ] 测试机有真实或造的 ai_usage_record 数据（至少一次 AI 调用产生记录）
- [ ] M0-4 的 adminRoutes 骨架在（usage 路由挂同一 adminRoutes 插件）

### 5.2 具体执行动作
1. **在 adminRoutes 插件加 usage 路由**：`GET /v1/admin/usage`，支持 `?from=&to=&device_id=` 过滤（方案 §3.4）。
2. **读表**：查询 `ai_usage_record` 表（B8），映射字段：providerId/providerName/modelId/modelName/inputTokens/outputTokens/totalTokens/sourceType/sourceId/sourceName/requestId/recordKind。
3. **数据准备**：在 188 测试机执行至少一次 AI 调用，确保 `ai_usage_record` 有真实记录；或用测试数据造一条。
4. **实测**：调 `GET /v1/admin/usage`，确认返回含模型名 + input/output/total token，字段完整（SDD 4.2 字段齐全）。

### 5.3 验收判据（V-M0-5）
| 判据 | 通过标准 | 验证方式 |
|------|---------|---------|
| 可读 | GET /v1/admin/usage 返回 200 | curl 实测 |
| 字段完整 | 含 providerId/modelId/inputTokens/outputTokens/totalTokens/sourceType（SDD 4.2 全字段） | 返回 JSON 核对 |
| 数据正确 | 读出的模型+token 与测试机实际调用一致 | 与 ai_usage_record 表比对 |

### 5.4 依赖
- 依赖：M0-3（基线）、M0-4（adminRoutes 骨架，usage 路由挂其上）
- 被依赖：F-7（usage 管理路由正式化）→ S-6（usage 采集上报）→ D-6（花费监控）→ M3

### 5.5 风险与兜底
- **风险（🟡 中）**：表字段在最新基线变化（B8 基于 v2.0.1，字段可能增删）；测试机无真实用量数据。
- **兜底**：字段按最新基线 `aiUsageRecord.ts` 重新核对；数据用测试用例造一条兜底；读表走 drizzle 官方查询不直写（只读无锁风险）。

---

## 六、依赖关系总览

```
M0-3(拉基线) ──┬─→ M0-1(热更新) ──→ F-9(热更新广播) ──→ M1 完成
               ├─→ M0-2(Windows构建) ──→ S-10/E-1/E-3 ──→ M2/M4
               ├─→ M0-4(管理路由写库) ──→ F-2/F-4/F-5 ──→ M1
               └─→ M0-5(usage读取) ──→ F-7 ──→ S-6 ──→ D-6 ──→ M3
```

| 任务 | 前置依赖 | 被谁依赖 |
|------|---------|---------|
| M0-1 热更新 | M0-3、M0-2（产物） | F-9、V-M1-4、**M1 门禁** |
| M0-2 Windows 构建 | M0-3 | S-10、E-1、E-3、M2/M4 |
| M0-3 拉基线 | 网络 | **全部 M0 其余 + F-1** |
| M0-4 管理路由写库 | M0-1、M0-3 | F-2/F-4/F-5、V-M1-1 |
| M0-5 usage 读取 | M0-3、M0-4 | F-7 → S-6 → D-6 → M3 |

**M0 总判据**（方案 13.1）：V-M0-1~V-M0-5 全绿；**任一项红则阻塞对应后续任务**。**V-M0-1（热更新）红时按方案 5.3 降级方案 C 强制重启兜底并重评，不达标不进入 M1。**

---

## 七、建议执行顺序（研发主管照此排期）

> 原则：**本机可做的先行（M0-3），需 Windows 测试机的（M0-1）等基线就绪后再做；构建（M0-2）与热更新验证（M0-1）可并行。**

| 序 | 任务 | 执行要点 | 资源 |
|----|------|---------|------|
| **1** | **M0-3 拉基线建分支** | 本机即可，`git fetch` + 建 managed/main，先确认无冲突 | 本机 + 网络 |
| **2** | **M0-2 Windows 构建** | GitHub Actions windows-latest 出 NSIS 包，可并行做 | CI + HOST_CHEN |
| **3** | **M0-1 热更新实测** | 基于 M0-3 基线做最小 IPC 广播验证；**最高优先级，老板硬要求** | HOST_CHEN HOST_188:23333 |
| **4** | **M0-4 管理路由对接** | 复用 M0-1 骨架扩展 agents/providers 路由，写库验证 | HOST_CHEN |
| **5** | **M0-5 usage 读取** | 挂 M0-4 adminRoutes，读 ai_usage_record | HOST_CHEN |

**并行建议**：M0-3 完成后，M0-2（CI 构建）与 M0-1（热更新）可并行推进；M0-1 是唯一阻塞 M1 的门禁项，应优先排期和资源保障。

---

## 八、验收状态追踪（品控官 shencha 执行）

- 每项验收结果记入看板任务（参照 TOOLS.md 看板工作流：主 Agent 派子 Agent → advance-state 推进）。
- 验收不通过 → 打回对应任务修复，复验。
- **热更新（V-M0-1）为老板硬要求，不达标不进入 M1**（方案 13.1/13.7）。
- 测试机：HOST_CHEN `HOST_188:23333`（跑非官方补丁版，M0 实测主用）；HOST_LIANG `HOST_69:23333`（备用/对账验证）。

---

## 九、变更记录

| 版本 | 日期 | 变更内容 | 来源 |
|------|------|---------|------|
| v1.0 | 2026-08-09 | 初始创建：M0 五项求证任务的可执行实施计划（前置/动作/验收/依赖/风险/顺序），基于方案 v4.0 + 源码校验报告 + 实际 cherry-src 基线核实 | 军师 guihua |