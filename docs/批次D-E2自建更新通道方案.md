# 批次 D · E-2 自建更新通道方案

> JJC-20260812-001（批次D）| 老板 2026-08-12 拍板「继续下一步」批准启动
> 权威定义：`任务分解-v4.0.md` E-2 + `sdd-企业受管版.md` 更新通道技术选型 + 源码校验报告（F-11 现状）
> 前置：批次 A 服务端骨架（master@59b94f7）+ 批次 B Sidecar 闭环（5f877ee）+ 批次 C SDD 修订（8b13293）
> 起草：主 Agent 基于权威文档 + electron-updater generic provider 机制（军师子 Agent 长任务易中断，改主 Agent 兜底产出，已在流程日志标注）

---

## 一、范围界定：批次 D 做 vs 不做

**批次 D = E-2 自建 generic electron-updater feed**：
- 在服务端主机（HOST_181）建 `patch_repo/` 静态目录，作为 electron-updater generic provider feed
- 提供 `latest.yml` 生成逻辑 + 安装包/patch 存放 + HTTP 访问
- 提供发布 API（上传新版本包 → 生成/更新 latest.yml）
- 验证 Fork（electron-updater generic provider）能从自建 feed 拉取升级

**明确归其他批次（本方案不做，仅留接口）**：
- E-1 GitHub Actions 构建流水线（批次 M4 范畴，本机 Linux 无 Windows 打包链）
- E-3 PyInstaller Sidecar 打包（批次 E）
- E-4 安装包集成（批次 M4）
- F-11 的 Fork 侧 feedURL 改指向（M1 已做部分，正式改指向归 M4 真机联调）
- D-6 花费监控、D-2 web 后台完整 UI（M3 范围）

**依赖**：F-11 已确认 Fork 用 `electron-builder.yml` publish `{provider: generic, url: https://releases.cherry-ai.com}`，可改 feedURL；批次 D 提供自建 feed 的 URL 供其指向。

---

## 二、electron-updater generic provider 机制（关键）

electron-updater **generic provider** 的 feed 约定（业界标准，源码校验报告确认 Fork 用它）：

- **feed URL** 是一个 HTTP 目录，根下放 `latest.yml`（Windows NSIS 用）+ 安装包（`*.exe`）
- **`latest.yml`** 结构（electron-builder 自动生成，generic provider 据此判断更新）：
  ```yaml
  version: 1.2.3
  files:
    - url: CherryStudio-Setup-1.2.3.exe
      sha512: <base64-sha512>
      size: 123456789
  path: CherryStudio-Setup-1.2.3.exe
  sha512: <base64-sha512>
  releaseDate: '2026-08-12T00:00:00.000Z'
  ```
- **更新判定**：Fork `autoUpdater.checkForUpdates()` → GET `{feed}/latest.yml` → 比对 version → 有新版则下载 `path` 指定的安装包并安装
- **多平台**：Mac 用 `latest-mac.yml`、Linux 用 `latest-linux.yml`；批次 D 只需 Windows（`latest.yml`），其余留空
- **增量 patch**：electron-updater 支持 `differential`（`.blockmap`），可选；批次 D 先做全量安装包，增量 patch（D-8）留批次 M4

**关键结论**：自建 feed = 一个静态 HTTP 目录 + 正确生成的 `latest.yml` + 安装包文件。**无需任何专属服务端逻辑**，Fork 的 electron-updater 原生兼容。

---

## 三、服务端实现（批次 A server/ 扩展）

在现有 FastAPI `server/`（HOST_181:2334）扩展：

### 3.1 目录结构
```
server/
├── patch_repo/                 # feed 根目录（static）
│   ├── latest.yml              # Windows 更新元数据（electron-builder 格式）
│   └── CherryStudio-Setup-<ver>.exe   # 安装包
└── main.py                     # 加静态挂载 + 发布 API
```

### 3.2 patch_repo 静态挂载
`main.py` 加：
```python
from fastapi.staticfiles import StaticFiles
app.mount("/patch_repo", StaticFiles(directory=PATCH_REPO_DIR), name="patch_repo")
```
- feed URL = `http://HOST_181:2334/patch_repo/`
- Fork feedURL 指向此（F-11 侧，批次 M4 落真机）

### 3.3 发布 API：POST /api/release/publish
接收新版本安装包，生成/更新 `latest.yml`：
```python
class ReleaseReq(BaseModel):
    version: str          # 如 "1.2.3"
    file_name: str        # 安装包文件名（放 patch_repo/ 内）
    size: int             # 字节
    sha512: str           # base64 sha512

@app.post("/api/release/publish")
async def publish_release(req: ReleaseReq, x_token: str = Header(None)):
    # 鉴权：x_token 必须匹配 config.json 的 token（防误覆盖/篡改 feed）
    # 校验 patch_repo/ 下存在该文件
    # 写 patch_repo/latest.yml：
    #   version / files[0].url / sha512 / size / path / releaseDate(ISO-8601)
    # 返回 {ok:true, latest_url, version}
```

### 3.4 config.json 扩展
```json
{ "patch_repo_dir": "./patch_repo" }
```

---

## 四、latest.yml 生成逻辑

```python
import hashlib, base64, datetime

def build_latest_yml(version, file_name, size, sha512_b64):
    return {
        "version": version,
        "files": [{"url": file_name, "sha512": sha512_b64, "size": size}],
        "path": file_name,
        "sha512": sha512_b64,
        "releaseDate": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
```
- `sha512` 需 base64 编码（electron-builder 约定），由发布方计算或服务端算
- 覆盖写 `latest.yml`（新版本即最新）

---

## 五、Fork 对接（记录，不落代码于本批次）

- F-11 已确认 Fork `electron-builder.yml` publish generic provider + `AppUpdaterService.ts` autoUpdater.channel 可配
- 批次 D 交付 feed URL `http://HOST_181:2334/patch_repo/`，Fork 侧改 feedURL 指向（正式改属 M4 真机联调，因需 Windows 真机验证）
- 本批次只保证 feed 本身正确（generic provider 原生兼容），不落 Fork 代码

---

## 六、验收标准（可衡量）

| # | 验收项 | 通过标准 |
|---|--------|---------|
| AC1 | patch_repo 静态挂载 | `GET /patch_repo/latest.yml` 返回 200 + 正确 yaml |
| AC2 | 发布 API | `POST /api/release/publish` 生成 latest.yml，字段完整（version/files/path/sha512/releaseDate） |
| AC3 | 安装包可下载 | `GET /patch_repo/<exe>` 返回 200 + 正确 Content-Length |
| AC4 | sha512 可校验 | 下载的 exe 计算 sha512 → base64 与 latest.yml 一致 |
| AC5 | 更新链路可验证 | electron-updater generic provider 逻辑：latest.yml 版本可被解析，path 指向存在文件 |
| AC6 | pytest | **server 既有 10 个 + 新增 feed 测试全过；sidecar 18 不回归**（口径：server 套件=10，sidecar=18，勿混） |
| AC7 | 版本升级语义 | 发布 v2 后 latest.yml 版本变为 v2，Fork 检测到新版（逻辑验证，真机归 M4） |

**边界/异常覆盖**：
- patch_repo 目录不存在 → 自动创建
- 发布时安装包文件缺失 → 400 报错，不写 latest.yml
- latest.yml 已存在 → 覆盖写（新版本即最新）
- 多版本共存 → patch_repo 保留历史 exe，latest.yml 只指最新（可选清理旧包）
- **AC3/AC4 需真实文件**：测试用 dummy 二进制占位（非真安装包）跑通机制；真机升级验证归 M4

---

## 七、风险与对策

| 风险 | 对策 |
|------|------|
| electron-updater generic 格式不匹配 | 严格按 electron-builder latest.yml 约定；AC4 校验 sha512 |
| sha512 需 base64（非 hex） | 发布 API 显式要求 base64，服务端校验可解码 |
| 安装包缺失导致下载 404 | 发布前校验文件存在（AC2 前置） |
| feed 与 Fork 真机联调延迟 | 本批次只保证 feed 正确，Fork 改 feedURL 归 M4 真机 |
| 增量 patch 未做 | 明确归批次 M4（D-8），本批次全量安装包 |

---

## 八、更新清单

| 文件 | 改动 |
|------|------|
| `server/main.py` | +patch_repo 静态挂载 + POST /api/release/publish |
| `server/config.json` | +patch_repo_dir |
| `server/tests/` | +test_feed.py（latest.yml 生成/校验/发布 API） |
| `docs/sdd-企业受管版.md` | 更新通道节标注自建 feed URL 落地 |
| 本方案文档 | 落盘 |

**不做**：Fork 代码改动、E-1/E-3/E-4、真机升级验证（M4）。

---

## 九、验收动作（本批次跑通后回填）

- [ ] AC1-AC5 本机跑通（POST publish → GET latest.yml → GET exe → sha512 校验）
- [ ] AC6 pytest 全过
- [ ] commit push GitHub + NAS 同步
- [ ] 看板推进到 Done 归档
