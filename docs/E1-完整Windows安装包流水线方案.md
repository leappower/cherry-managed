# JJC-20260813-001 · E-1 完整 Windows 安装包流水线 — 执行方案

> 2026-08-13 | Fork 源码：`/home/chee/.openclaw/workspace-main/cherry-src`（remote managed → leappower/cherry-studio-managed）
> Sidecar/服务端：`/home/chee/Projects/cherry-managed`（remote github → leappower/cherry-managed）
> 权威定义：`任务分解-v4.0.md` E-1（GitHub Actions 构建流水线）
> 起草：主 Agent（军师子 Agent 长任务易中断，主 Agent 兜底产出）

---

## 一、目标

把 `fork-win-build.yml`（现有 M0-2 雏形：仅 build Fork 出 NSIS 包）升级为 **E-1 完整 Windows 安装包流水线**：GitHub Actions windows-latest runner 上，一次性产出**完整企业受管安装包**（Fork + Sidecar + 受管标记 + 卸载器）。

## 二、现状核实（已实读代码）

### cherry-src（Fork）
- `electron-builder.yml` **E-4 已埋 Sidecar 集成点**：
  ```yaml
  win:
    extraResources:
      - from: "${env.SIDECAR_EXE_PATH}"   # E-1 流水线设此 env 即注入
        to: "sidecar"                       # → 安装后 resources/sidecar/
  ```
- `build/nsis-installer.nsh` E-4 已实现卸载器（NSSM stop/remove + 清 managed_registry.db）
- **受管标记**：`CHERRY_MANAGED_BUILD=1`（src/main/services/AppUpdaterService.ts:160 读取，M1 已实现）
- `fork-win-build.yml`（M0-2 雏形）：windows-latest + setup-node 24.11.1 + pnpm install + `pnpm build:win:x64` + upload NSIS installer。**当前无 Sidecar 集成、无受管标记**
- `build:win:x64` = `dotenv pnpm run build && electron-builder --win --x64`
- remote：`managed`（leappower/cherry-studio-managed 私有）+ `origin`（CherryHQ/cherry-studio 官方）

### cherry-managed（Sidecar）
- `.github/workflows/build_windows.yml`（批次 E）：windows-latest + pyinstaller 出 `dist/sidecar.exe`
- Sidecar 源码在**另一仓库**（cherry-managed），Fork 流水线需检出两仓库

## 三、核心设计：单 workflow 两段构建

**方案：升级 fork-win-build.yml 为「单 workflow，两段构建，Sidecar 注入 Fork」**

```yaml
jobs:
  build-win:
    runs-on: windows-latest
    steps:
      # ① 检出 Fork（当前仓库）
      - uses: actions/checkout@v6

      # ② 检出 Sidecar 源码（cherry-managed 私有仓库，需 PAT）
      - name: Checkout Sidecar repo
        uses: actions/checkout@v6
        with:
          repository: leappower/cherry-managed
          path: _sidecar
          token: ${{ secrets.CHERRY_MANAGED_PAT }}

      # ③ 构建 Sidecar.exe（pyinstaller，复用 build_windows.yml 逻辑）
      - name: Build Sidecar exe
        shell: bash
        run: |
          cd _sidecar/sidecar
          pip install pyinstaller websocket-client
          pyinstaller --clean --noconfirm --onefile \
            --specpath scripts --distpath ../dist --workpath ../build scripts/build.spec
          cp ../dist/sidecar.exe "$GITHUB_WORKSPACE/sidecar.exe"

      # ④ 安装 Fork 依赖
      - uses: pnpm/action-setup@v5
      - run: pnpm install

      # ⑤ 构建 Fork 完整安装包（注入 Sidecar + 受管标记）
      - name: Build managed Windows installer
        shell: bash
        run: pnpm build:win:x64
        env:
          SIDECAR_EXE_PATH: "${{ github.workspace }}/sidecar.exe"   # E-4 extraResources 注入
          CHERRY_MANAGED_BUILD: '1'                                  # 受管标记（M1）
          CSC_IDENTITY_AUTO_DISCOVERY: 'false'                       # 禁用签名（M0-2 已定）
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          NODE_OPTIONS: --max-old-space-size=8192

      # ⑥ 上传完整 NSIS 安装包（含 Sidecar）
      - uses: actions/upload-artifact@v7
        with:
          name: cherry-studio-managed-setup
          path: dist/*-setup.exe
```

## 四、关键决策（NEEDS CLARIFICATION）

| # | 决策点 | 建议 |
|---|--------|------|
| K1 | **多仓库检出认证**：Sidecar 在 cherry-managed（私有），Fork 流水线需检出 → 需 PAT secret `CHERRY_MANAGED_PAT` | 在 cherry-studio-managed 仓库 Actions secrets 配 leappower PAT（对 cherry-managed 有读权限） |
| K2 | **触发方式**：当前仅 workflow_dispatch + push managed/** | 加 tag `v*` 触发（发布时）+ 保留手动 |
| K3 | **Sidecar 构建来源**：每构建现打 sidecar.exe（本方案）vs 从 cherry-managed Releases 下载 | 现打（最简，无版本耦合） |
| K4 | **受管标记注入**：构建期 env `CHERRY_MANAGED_BUILD=1`（本方案）vs 安装期写入 | 构建期 env（M1 已定语义） |
| K5 | **是否发布到自建 feed**（E-2 批次 D 已建 patch_repo）：本 workflow 顺带 POST /api/release/publish？ | 本轮只出包 + artifact，feed 发布归 M4（需服务端 token） |

## 五、验证（本任务可做）

- **AC1**：fork-win-build.yml 语法正确（yaml lint / actionlint）
- **AC2**：workflow 含两段构建（Sidecar pyinstaller + Fork electron-builder）+ SIDECAR_EXE_PATH 注入 + CHERRY_MANAGED_BUILD=1
- **AC3**：PAT secret 名与 workflow 引用一致（CHERRY_MANAGED_PAT 文档化，仓库需配置）
- **AC4**：构建产物 artifact 名明确（cherry-studio-managed-setup）
- **AC5**：E-4 配置不破坏（electron-builder.yml extraResources SIDECAR_EXE_PATH 仍引用 env）
- **AC6**：sidecar pytest 18 不回归 + cherry-src 无破坏（若改动 cherry-src）

**真跑验证**：本地无 Windows 打包链，workflow 实际执行需 GitHub Actions 触发（push 到 managed 分支或手动）→ 归 M4 或本任务末尾触发一次验证

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| 多仓库 checkout 私有认证失败 | PAT secret（CHERRY_MANAGED_PAT）+ 文档化配置步骤 |
| sidecar.exe 注入路径不对 | SIDECAR_EXE_PATH 用 $GITHUB_WORKSPACE 绝对路径；验证 resources/sidecar/ 存在 |
| 签名阻塞 | CSC_IDENTITY_AUTO_DISCOVERY=false（M0-2 已定） |
| 构建慢（两段打包） | windows-latest + pnpm cache + 依赖缓存 |
| 官方 upstream 冲突 | fork-win-build.yml 只新增不改官方 workflow |

## 七、更新清单
- `cherry-src/.github/workflows/fork-win-build.yml`（升级：两段构建 + Sidecar 注入 + 受管标记 + 多仓库 checkout）
- `docs/E1-完整Windows安装包流水线方案.md`（本方案）
- `cherry-studio-managed` 仓库：配置 Actions secret `CHERRY_MANAGED_PAT`（文档化，需老板/管理员配）

## 八、验收动作（跑通后回填）
- [ ] workflow 语法 + 两段构建 + Sidecar 注入 + 受管标记
- [ ] push 到 managed 分支触发一次真实构建（验证出包 + resources/sidecar/ 存在）
- [ ] sidecar pytest 18 回归
- [ ] commit push + NAS 同步
- [ ] 看板推进 Done 归档
- [ ] M4：真实安装 + 升级验证（需 Windows 机）