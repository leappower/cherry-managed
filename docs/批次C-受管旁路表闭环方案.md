# CherryStudio 企业受管版 · 批次 C：SDD schema 修订 + 受管旁路表闭环

> 版本：批次 C 方案（2026-08-12）
> 看板：JJC-20260812-001（M2 批次 C）
> 前置：决策 iii 已拍板（老板）——受管旁路表统一为 `managed_entity(kind, id, created_at)`
> 目标：修订 SDD 文档对齐 iii 决策，打通「Sidecar 写 → 存储 → Fork 读」受管旁路表闭环，确认双端一致

---

## 一、问题陈述（为什么做批次 C）

### 1.1 决策 iii 与文档分裂

- **决策 iii（已拍板）**：受管旁路表统一为 `managed_entity(kind, id, created_at)`。
- **M1 Fork 层已落地**：`ManagedRegistryService.ts` 用 `managed_entity(kind,id,created_at)` schema（含 cherryai 种子 + 内嵌 fallback），路径 `{userData}/Data/managed_registry.db`。
- **M2 Sidecar S-8 已落地**：`sidecar/managed_registry.py` 写 `managed_entity(kind,id,created_at)`（本批次 B 已确认，见文件头注释）。
- **但文档 `docs/sdd-企业受管版.md` 第 4.1 节仍是旧 schema**：

```sql
-- ❌ 旧（文档，已过时）
CREATE TABLE managed_registry (
  id TEXT PRIMARY KEY,      -- 语义漂移：只按 id 主键，无法区分同 id 的 provider/agent
  type TEXT NOT NULL,       -- 字段名与实现不一致（实现用 kind）
  managed INTEGER NOT NULL DEFAULT 1,  -- 多余列（实现无）
  created_at TEXT
);
```

**矛盾点**：
1. 表名 `managed_registry` vs 实现 `managed_entity`
2. 字段 `id`(PK) + `type` vs 实现 `kind` + `id`(复合 PK)
3. 多余 `managed INTEGER` 列（实现没有，语义被「登记即受管」取代）
4. 主键语义：旧版单 id 主键无法区分同 id 的 provider 与 agent；新版 `(kind,id)` 复合主键解决

### 1.2 影响

文档与实现分裂会导致：新读者按旧 schema 写错查询、Sidecar 与服务端对账口径不一、后续 M3 服务端/D-6 花费监控对接时误解数据结构。**批次 C 必须修订文档对齐实现（iii）**。

---

## 二、修订后的 SDD 4.1 数据模型（权威）

### 2.1 managed_entity（受管旁路表，sqlite，Sidecar 唯一写者）

```sql
CREATE TABLE managed_entity (
    kind       TEXT NOT NULL,   -- 'agent' | 'provider' | 'skill' | 'mcp'
    id         TEXT NOT NULL,   -- 受管项 id（provider/agent/skill/mcp 的 id）
    created_at TEXT,            -- ISO-8601 UTC 登记时间
    PRIMARY KEY (kind, id)
);
```

**语义要点**：
- **不动官方 schema，无迁移锁库风险**（延续 Q7/Q-A3 决策）。
- **Sidecar 是本表唯一写者**；Fork 渲染层只读。
- **登记即受管**：表中存在的 (kind,id) 即受管项；不存在的即员工自配非受管。无 `managed` 布尔列（冗余）。
- **复合主键 `(kind,id)`**：同一 id 可分别以 provider/agent/skill/mcp 登记，互不冲突。
- **kind 枚举**：`agent` | `provider` | `skill` | `mcp`。
- **位置**：`{userData}/Data/managed_registry.db`（与 M1 Fork 读取路径一致）。

### 2.2 与 M1 Fork `ManagedRegistryService.ts` 对齐

| 维度 | M1 Fork 实现（已落地） | 本方案（批次 C 对齐） |
|------|----------------------|---------------------|
| 表名 | `managed_entity` | `managed_entity` ✅ |
| 字段 | `kind, id, created_at` | `kind, id, created_at` ✅（字段名一致）|
| 主键 | `(kind, id)` | `(kind, id)` ✅ |
| 路径 | `{userData}/Data/managed_registry.db` | 同 ✅ |
| **created_at 类型** | **`INTEGER NOT NULL`（epoch 毫秒，`Date.now()`）** | **本批次定稿：统一为 INTEGER epoch 毫秒**（见 §3.4）|
| 写者 | Fork 只读（M1 仅种子初始化，非运行时写） | Sidecar 唯一写者 |
| cherryai 种子 | Fork 首次创建时内嵌 cherryai 受管种子 | Sidecar 不覆盖种子，UPSERT 幂等 |
| 兜底 fallback | db 缺失/损坏/测试环境 → cherryai 恒受管 | 保留（Fork 侧逻辑） |

---

## 三、受管旁路表闭环设计

### 3.1 三层一致性

```
┌─────────────────────────────────────────────────────────┐
│  写者：Sidecar（S-8 managed_registry.py，唯一写者）        │
│  - dispatch 成功 → mark_managed(kind, id)（幂等 UPSERT）  │
│  - 回收/删除/禁用 → unmark(kind, id)                     │
│  - 全量对账重建 → clear() + 按期望清单重登记               │
└────────────────────────┬────────────────────────────────┘
                         │ 写 {userData}/Data/managed_registry.db
┌────────────────────────▼────────────────────────────────┐
│  存储：managed_entity(kind,id,created_at) 单文件 sqlite   │
└────────────────────────┬────────────────────────────────┘
                         │ 读（只读）
┌────────────────────────▼────────────────────────────────┐
│  读者：Fork 渲染层 ManagedRegistryService.ts              │
│  - isManaged(kind,id) → 受管项锁死 UI / 隐藏删除          │
│  - 对账 reconcile 依据本表判定「受管保护」vs「员工自配」     │
└─────────────────────────────────────────────────────────┘
```

### 3.2 写时序

1. **派发登记**：Sidecar 收到 `dispatch_agent`（create/update）→ 调 Fork 管理路由成功 → 返回 `dispatch_result success` → `mark_managed(kind, id)` 登记。
2. **回收注销**：`dispatch_agent`（delete/disable）或 `dispatch_provider`（remove）成功 → `unmark(kind, id)`。
3. **全量对账**：`reconcile` 用 `clear()` 重建，再按服务端期望清单重登记，保证旁路表与服务端期望一致。

### 3.3 冲突/幂等规则

- **mark_managed 幂等**：`ON CONFLICT(kind,id) DO UPDATE SET created_at=excluded.created_at`（已实现）。
- **同 id 多 kind 不冲突**：复合主键天然隔离。
- **Sidecar 不覆盖 Fork 种子**：UPSERT 只更新 created_at，不删除 cherryai 种子项；Fork 兜底 fallback 保持恒受管。

### 3.4 created_at 类型定稿（审计修正）

**审计挖出的双端不一致**：
- Sidecar 原实现：`created_at TEXT`（ISO-8601 字符串）
- M1 Fork 已落地：`created_at INTEGER NOT NULL`（epoch 毫秒，`Date.now()`）

**定稿决策**：统一为 **`INTEGER`（epoch 毫秒）**，与 Fork 已落地一致，避免改 Fork。

```sql
CREATE TABLE managed_entity (
    kind       TEXT NOT NULL,
    id         TEXT NOT NULL,
    created_at INTEGER NOT NULL,   -- epoch 毫秒
    PRIMARY KEY (kind, id)
);
```

Sidecar `managed_registry.py` 同步改动：`_now()` 返回 `int(time.time()*1000)`，`created_at` 列改 `INTEGER NOT NULL`；测试断言相应更新。

> 注：`created_at` 仅排序/展示用，不参与 `isManaged` 判定（只读 kind+id），但类型必须统一以保证双端互读与排序一致。

---

## 四、双端一致性校验

目标：确认 Sidecar 写的旁路表和 Fork 读的旁路表是**同一个文件、同一 schema、同一格式**。

### 4.1 校验清单

| 校验项 | 方法 | 通过标准 |
|--------|------|---------|
| 路径一致 | 对比 S-8 `managed_registry.py` 默认路径 vs Fork `ManagedRegistryService.ts` 路径 | 均为 `{userData}/Data/managed_registry.db` |
| 表名一致 | 读 S-8 SCHEMA vs Fork 建表 DDL | 均为 `managed_entity` |
| 字段一致 | 对比列名/类型/主键 | `kind,id,created_at`，PK(kind,id) |
| 格式一致 | 各插入一条，对比两读 | 行内容 `{kind,id,created_at}` 可互读，且 `created_at` 均为 INTEGER（epoch 毫秒）|
| 端到端 | Sidecar 写 → 用 Fork 的读逻辑查 | 能读到刚登记的受管项 |

### 4.2 端到端验证命令（可执行）

```bash
# 1. Sidecar 侧登记一个受管项
cd sidecar && python3 -c "
from managed_registry import ManagedRegistry
r = ManagedRegistry('data/Data/managed_registry.db')
r.mark_managed('agent', 'e2e-check-001')
print('登记:', r.all())
"
# 2. 用同一 schema 读回（模拟 Fork 只读）
python3 -c "
import sqlite3
c = sqlite3.connect('sidecar/data/Data/managed_registry.db')
print('读回:', c.execute('SELECT kind,id FROM managed_entity').fetchall())
"
# 3. 清理
python3 -c "
from managed_registry import ManagedRegistry
r = ManagedRegistry('sidecar/data/Data/managed_registry.db')
r.unmark('agent', 'e2e-check-001')
"
```

> 注：M1 Fork 为独立 cherry-src 工程，批次 C 本机验证聚焦 Sidecar 侧写 + schema 对齐；Fork 真机读侧验证归批次 F 真机测试。

---

## 五、更新清单（逐条）

| # | 文件 | 改动 | 优先级 |
|---|------|------|--------|
| 1 | `docs/sdd-企业受管版.md` §4.1 | 旧 `managed_registry` schema → 新 `managed_entity(kind,id,created_at)`；补 2.2 对齐表 | 🔴 必改 |
| 2 | `docs/sdd-企业受管版.md` §3.x 架构/§7 质询 | 若提及旧表名/字段，同步替换 | 🟡 检查 |
| 3 | `docs/任务分解-v4.0.md` S-8 | 更新 S-8 描述为 `managed_entity(kind,id,created_at)`（如文中是旧 schema） | 🟡 检查 |
| 4 | `docs/方案-企业受管版-v4.0.md` | 方案文档如有旧 schema 引用，同步 | 🟡 检查 |
| 5 | `sidecar/managed_registry.py` | **无需改**（已是新 schema，批次 B 已对齐） | ✅ 无 |
| 6 | `sidecar/tests/test_*.py` | 若测试断言旧字段名，更新；无则保留 | 🟡 检查 |

---

## 六、风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| M1 已用 managed_entity，如有历史数据（旧 managed_registry 表）| 🟡 中 | 若存在旧表且需迁移：新建 managed_entity + 数据搬移 + 删旧表；当前 M1/M2 均为新工程，无历史数据，直接采用新 schema |
| 文档与实现不同步（本批次根因复发）| 🔴 高 | 批次 C 修订后，建立「schema 变更必须同步 SDD + 代码」铁律；M3/D-6 对接前复核 |
| 双端路径不一致（Sidecar vs Fork 读到不同文件）| 🟡 中 | 用 §4.1 校验清单在批次 F 真机验证前先行本机校验；Sidecar 配置 `paths.user_data` 与 Fork `{userData}` 需对齐 |
| **created_at 类型双端不一致（Sidecar TEXT vs Fork INTEGER）** | 🔴 高 | **本批次已定稿统一为 INTEGER epoch 毫秒（§3.4）**，Sidecar 代码+测试同步改；批次 F 真机前用 §4.1「格式一致」复核 |
| Sidecar 误删 Fork 种子（cherryai）| 🟡 中 | mark_managed 为 UPSERT 不删；clear() 仅限全量重建场景，Fork 兜底 fallback 保 cherryai 恒受管 |

---

## 七、验收标准（批次 C AC）

| # | 验收项 | 通过标准 |
|---|--------|---------|
| 1 | SDD §4.1 已修订 | `managed_entity(kind,id,created_at)`，无旧 `managed_registry(id,type,managed)` |
| 2 | 对齐表存在 | §2.2 明确 M1 Fork 与 Sidecar 对齐维度 |
| 3 | 闭环设计完整 | §3 覆盖写者/存储/读者 + 时序 + 幂等 |
| 4 | 一致性校验可执行 | §4.2 命令本机跑通（登记→读回→清理），且 `created_at` 类型已统一为 INTEGER |
| 5 | 更新清单落地 | §5 所列文件均处理（必改项完成）|
| 6 | 文档可追踪 | 本方案文件 + SDD 修订后 commit 到 cherry-managed |

---

## 八、范围外（非本批次）

- Fork 侧 ManagedRegistryService.ts 代码改动（M1 已定稿，本批次只对齐文档）
- 真机端到端读侧验证（批次 F）
- M3 服务端 Web 后台 / D-6 花费监控对接（依赖本批次 schema 定稿）
