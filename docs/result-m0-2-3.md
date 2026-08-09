# M0 求证阶段 · 本机可做部分执行结果（M0-3 + M0-2）

> **执行者**：研发主管 daima | **日期**：2026-08-09 | **状态**：M0-3 ✅ / M0-2 ✅（workflow 层）｜M0-1/M0-4/M0-5 🔴 阻塞（测试机不可达）
> **依据**：plan-m0.md（§二 M0-2 / §三 M0-3）+ 审计修正 + 源码校验报告-v2.0.1 + 方案-企业受管版-v4.0 §13.1
> **基线说明**：M0-3 已把 cherry-src 从 v2.0.1（f39b17d，落后 21 commits）拉到最新 origin/main（12498d6）。

---

## 一、M0-3 拉官方最新基线建 Fork 分支（V-M0-3）— ✅ 完成

### 1.1 执行结果

| 步骤 | 结果 |
|------|------|
| 确认最新基线 | `git ls-remote origin refs/heads/main` → **`12498d68ecb4fb261670843ca7a8e4e64a37526a`** |
| 确认落后量 | 拉取前本地 `f39b17d`，`git log --oneline HEAD..origin/main | wc -l` = **21 commits** |
| 拉最新 | `git pull --ff-only origin main` → 成功快进到 12498d6 |
| 建 managed 分支 | `git checkout -b managed/main origin/main` → 分支创建，跟踪 origin/main |
| rebase 干净性验证 | `git rebase origin/main` → **「当前分支 managed/main 是最新的」，REBASE_EXIT=0，无冲突** |

### 1.2 最新基线 SHA
- **rebase 基线（origin/main 实时顶端）** = `12498d68ecb4fb261670843ca7a8e4e64a37526a`；当前分支 HEAD（fork 工作流提交）= `11149cee4`
- 本地 lag 归零（`git rev-list --left-right --count origin/main...managed/main` = `0 1`，managed/main 仅在顶部多出 M0-2 的 workflow 提交，无落后）

### 1.3 分支状态
```
* managed/main          ← Fork 改动基线（方案 D18 分支策略）
  main
  remotes/origin/main   ← 12498d6（官方镜像）
```
- `managed/main` 已建，跟踪 origin/main，rebase 干净无冲突。

### 1.4 V-M0-3 验收判定：**✅ 通过**
| 判据 | 标准 | 结果 |
|------|------|------|
| 已拉最新 | rebase 基线 = origin/main 最新 SHA | ✅ 12498d6（HEAD=11149cee4 叠于其上） |
| managed 分支存在 | managed/main 已建 | ✅ |
| rebase 干净 | 从最新基线 checkout 无冲突 | ✅ REBASE_EXIT=0 |

### 1.5 后续注意（按 plan §3.5）
基线从 f39b17d→12498d6 已漂移，**M0-1/M0-4/M0-5 实施时必须按最新基线重新核对注入点（方案 §6.2），不直接套用 v2.0.1 行号**。

---

## 二、M0-2 Windows 补丁包构建 workflow（V-M0-2）— ✅ workflow 层完成，CI 待实测

### 2.1 交付文件
- **路径**：`cherry-src/.github/workflows/fork-win-build.yml`（已提交到 `managed/main`，commit `11149cee4`）
- **内容**：`windows-latest` runner → checkout → setup-node(.node-version=24.11.1) → pnpm 11.8.0 → `pnpm install` → `pnpm build:win:x64` → 上传 NSIS artifact。
- **触发**：`workflow_dispatch`（手动）+ `push` 到 `managed/**`。
- **产物**：`dist/*-setup.exe`（NSIS，artifactName `${productName}-${version}-${arch}-setup.${ext}`），retention 14 天，`if-no-files-found: error`（出包失败即显式报错）。

### 2.2 签名处理（审计修正落实）— 采用「临时禁用签名」方案
**审计修正原文**：`electron-builder.yml:98-99` 有 `signtoolOptions.sign=scripts/win-sign.js`，官方 workflow 用 `CSC_LINK`/`CSC_KEY_PASSWORD` secrets，新建 fork-win-build.yml 若不配签名 secrets 会阻塞出包。

**执行核实与决策**（基于最新基线源码，非臆断）：
- `electron-builder.yml:98-99` 确认存在 `signtoolOptions: { sign: scripts/win-sign.js }`，`verifyUpdateCodeSignature: false`。
- **关键事实**：`scripts/win-sign.js:60` 签名逻辑**整体包在 `if (process.env.WIN_SIGN)` 内** —— 只有设置了环境变量 `WIN_SIGN` 才执行签名，未设置时是 no-op，**不会阻塞构建**。
- **官方先例佐证**：官方 `.github/workflows/nightly-build.yml` 的 Windows job **不设 `CSC_LINK`/`CSC_KEY_PASSWORD`/`WIN_SIGN`**，Windows 包不签名照样出包；只有 Mac job 才配 CSC/Apple secrets。
- **因此采用的方案**：本 workflow **明确不设置 `WIN_SIGN`，不配置 `CSC_*` secrets → 临时禁用签名**，出包不被签名阻塞。
  - 兜底加固：显式设 `CSC_IDENTITY_AUTO_DISCOVERY=false`，防止 electron-builder 自动发现 runner 本机证书而意外触发签名。
  - `WIN_SIGN`/`CSC_*` 仅出现在 workflow 注释说明中，**未作为任何 env 变量注入**（已用字节级校验确认）。

**说明**：未签名包在 Windows SmartScreen 会提示"未知发布者"，仅用于 M0 求证阶段内网测试机（188/69）验证；正式发布签名属后续 F-11/E-2 范围，届时引入受管签名（`WIN_SIGN` + 证书 secrets）。

### 2.3 本机构建可行性结论 — **本机不可行，标注需 CI 实测**
- 本机为 **Linux x86_64**，无 wine/wine64，无 Windows 交叉打包链；`node_modules` 未安装。
- electron-builder 在 Linux 上出 Windows NSIS 包需 wine 或 Windows runner，本机无法直接跑通 `pnpm build:win:x64`。
- **符合 plan M0-2 §2.5 兜底原文**：「本机 Linux 侧无法直接出 Windows 包……若 CI 长期不可用，退化为手动在任一 Windows 机执行 `pnpm build:win:x64`（测试机 188/69 可代跑）」。
- **结论**：本轮仅在 **workflow 层验证**（YAML 语法 PyYAML 校验通过、trigger/env/artifact 结构完整），**需 GitHub Actions CI 实测出包**；或网络恢复后由测试机 188/69 代跑。

### 2.4 workflow 语法/结构校验结果
| 校验项 | 结果 |
|--------|------|
| YAML 语法 | ✅ PyYAML `yaml.safe_load` 解析通过 |
| `on` 触发 | ✅ `workflow_dispatch` + `push: branches: managed/**` |
| env 注入 | ✅ 仅 `CSC_IDENTITY_AUTO_DISCOVERY=false`、`GH_TOKEN=${{ secrets.GITHUB_TOKEN }}`（字节级确认 intact）、`NODE_OPTIONS`；**无 WIN_SIGN/CSC_LINK** |
| upload-artifact | ✅ `dist/*-setup.exe`，`if-no-files-found: error` |
| actionlint | ⚠️ 本机未装，无法跑 actionlint；已用 YAML 解析 + 结构人工核验替代 |

### 2.5 V-M0-2 验收判定：**⚠️ 部分通过（workflow 完整，CI 出包待实测）**
| 判据 | 标准 | 结果 |
|------|------|------|
| CI 出包 | windows-latest 跑 build:win:x64 无报错 | ⏳ 待 CI 实测（本机 Linux 无法代跑） |
| NSIS 产物 | 产出 `${productName}-${version}-x64-setup.exe` | ⏳ 待 CI 实测（artifact 上传逻辑已配） |
| 可安装启动 | NSIS 包在 188 安装并启动 | 🔴 阻塞（188 不可达） |
| **workflow 文件/语法/签名处理** | 文件存在、语法正确、含签名处理 | ✅ 已完成 |

---

## 三、阻塞项标注（M0-1 / M0-4 / M0-5 待网络恢复）

- **测试机实测**（M0-1 热更新、M0-4 管理路由、M0-5 usage 读取）：均需 HOST_CHEN `HOST_188:23333` 或 HOST_LIANG `HOST_69:23333` 可达。
- **本轮实测**：`curl http://HOST_188:23333/health` 与 `HOST_69:23333/health` 均 **HTTP 000 / 不可达**。
- **结论**：M0-1/M0-4/M0-5 本轮不执行，等网络恢复后按 plan-m0.md §一/§四/§五 执行。
  - M0-1 为老板硬要求、唯一阻塞 M1 的门禁项，网络恢复后应**优先排期**。
  - 基线已拉到最新（12498d6），执行 M0-1/M0-4 时**按最新基线重新核对注入点**（不套用 v2.0.1 行号）。
  - **M0-1 invalidateQueries 命名修正**（审计记录，下轮 M0-1 用）：源码真实 API 是 `useDataChange` / `useInvalidateCache`，非 `invalidateQueries`；M0-1 实施时按真实 API 接线。

---

## 四、本轮 M0 完成度总览

| M0 任务 | 状态 | 说明 |
|---------|------|------|
| **M0-3 拉基线建分支** | ✅ 完成 | managed/main 已建，rebase 基线=12498d6（HEAD=11149cee4 叠于其上），rebase 干净 |
| **M0-2 Windows 构建 workflow** | ✅ workflow 完成 / ⏳ CI 待实测 | fork-win-build.yml 已建并提交，签名处理已落实，本机 Linux 无法出 Windows 包 |
| M0-1 热更新 | 🔴 阻塞 | 测试机 188/69 不可达 |
| M0-4 管理路由写库 | 🔴 阻塞 | 依赖 M0-1 + 测试机 |
| M0-5 usage 读取 | 🔴 阻塞 | 依赖 M0-4 + 测试机 |

---

## 五、产出文件与仓库状态

- **结果文档**：`/mnt/chee_2/Chee/OpenClaw_C/基建/cherry-managed/docs/result-m0-2-3.md`（本文件）
- **workflow 文件**：`/home/chee/.openclaw/workspace-main/cherry-src/.github/workflows/fork-win-build.yml`
- **提交记录**：`managed/main` = `11149cee4`（workflow）叠于 `12498d68e`（官方最新 main）
- **仓库**：`/home/chee/.openclaw/workspace-main/cherry-src`

---

## 六、变更记录

| 版本 | 日期 | 变更内容 | 来源 |
|------|------|---------|------|
| v1.0 | 2026-08-09 | M0-3/M0-2 本机可做部分执行结果；M0-1/4/5 标注测试机阻塞 | 研发主管 daima |