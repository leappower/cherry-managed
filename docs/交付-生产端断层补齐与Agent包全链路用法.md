# 交付说明：Agent 包全链路用法（生产端断层已补齐）

> 日期：2026-08-23
> 版本：对应 cherry-managed commit `9b835b6`（master）
> 一句话：**你负责做 Agent 包、把包变成文件、把文件喂给服务器；服务器负责下发；员工机负责接收使用。** 以前只有「下发→使用」，现在「制作→上传」这段也通了。

---

## 一、这条链路是什么（先看懂再动手）

```
① 制作 Agent  →  ② 上传服务器  →  ③ 服务器下发  →  ④ 员工端使用
   你/任意机器      管理后台上传        一键推送          员工机 sidecar 接收
```

- **① 制作**：在任何一台装有 CherryStudio（或用了我们受管版）的机器上，把 Agent 调好（name / type / model / description / instructions 提示词）。
- **② 上传**：两种方式（本轮新补的能力）：
  - **导成 .json 文件** → U盘/邮件/拖拽 → 管理后台「从 .json 包文件导入」
  - **从员工机直接导入** → 管理后台选设备 → 列出该机 Agent → 导入为配置包
- **③ 下发**：管理后台「推送 Agent」选配置包 → 推给指定员工机。
- **④ 使用**：员工机 sidecar 接收、落盘、重启 CherryStudio 后生效。

> 本轮补的关键缺口：**② 之前完全没做**。以前服务器只能「下发内置的包」，没法接收你做的新包。现在补上了「导出 .json / 上传 .json / 从设备导入」三条生产端进料口。

---

## 二、管理后台入口

- **地址**：`http://192.168.3.181:2334/admin/`（生产；本地开发 `http://127.0.0.1:2334/admin/`）
- **账号**：admin / admin123
- **包配置 tab**：负责「做包、导包、导包、发包」
- 8 个 tab：包配置 / 推送Agent / 设备 / 派发日志 / 用量 / 审计 / Agent清单 / 对账

---

## 三、操作手册（照做就行）

### 场景 A：做一个全新的 Agent 包并下发
1. 进「包配置」tab → 右侧「新建配置包」表单
2. 填：名称(metadata.name) / 版本(x.y.z) / Agent名 / type / model / description / instructions
3. 点「创建(rev=1)」→ 出现在左侧「① 配置包列表」
4. 去「推送Agent」tab → 选这个包 → 选目标设备 → 推送

### 场景 B：把别人机器上做好的 Agent 变成包（从设备导入）
1. 「包配置」tab → 顶部「📥 从设备导入 Agent」
2. 选一台员工机 → 「列出 Agent」→ 下拉里选一个 → 「📥 导入为配置包」
3. 导入后自带 id，可直接推送升级。包上会带 `source_device` / `source_agent_id` 溯源。

### 场景 C：把包导出成 .json 文件（跨机器/跨网络搬运）
1. 「包配置」tab → 左侧列表里，每个包行尾的「⬇」按钮
2. 浏览器下载 `<包名>.agent.json`
3. 这个文件就是**一份完整的 Agent 包**，可以拷给任何人 / 任何服务器。

### 场景 D：把 .json 文件导入成服务器上的包（上传）
1. 「包配置」tab → 顶部「📄 从 .json 包文件导入」
2. 选文件 → 「⬆ 上传导入」
3. 成功 → 变成本服务器可下发的配置包。同名包会提示「已存在」，用删除或更新处理。

### 场景 E：更新一个已有包（rev+1，留历史）
1. 「包配置」tab → 点左侧某个包 → 右侧进入「编辑配置包」
2. 改内容 → 「保存为新版本(rev+1)」→ 历史保留在「版本历史」，可回滚

---

## 四、给老板的三条使用要点（最核心）

1. **包就是文件**：一个 `.agent.json` 就是一份完整的 Agent 包。你能导出它、发它、导回来。这就是「封装」的实体。
2. **服务器是仓库**：所有包进服务器（导入/设备导入/新建）→ 由服务器统一下发。别在员工机之间直接传。
3. **下发后自动生效**：推给员工机后，那边 sidecar 接收落盘，重启 CherryStudio 即用。无需员工机手动操作。

---

## 五、本轮技术交付物（给开发/运维）

### 新增 API（server/main.py，commit `199f46a`）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/agent-configs/{name}/export` | 导出包为 .json（含完整 metadata+agent） |
| POST | `/api/admin/agent-configs/import` | 上传 .json 导入为包（同名 409） |
| POST | `/api/admin/devices/{device_id}/agent-import` | 从设备列出/导入 Agent（commit `6a6f7b3`，随新 sidecar exe） |

**鉴权**：`X-Admin-Token: <token>`（**不是** `Authorization: Bearer`）

### 管理后台 UI（commit `9b835b6`）
- 包列表每行「⬇ 导出」按钮
- 新建视图顶部「📄 从 .json 包文件导入」卡
- 「📥 从设备导入 Agent」区块（commit `069e023`）

### sidecar 打包（本章重点）
- **新 exe 已装回 40 机**：`D:\Cherry Studio\resources\sidecar\sidecar.exe`
  - 旧版备份：`sidecar.exe.bak_20260823`
  - NSSM 服务 `CherrySidecar` → **RUNNING**，device_id `managed-61af1a78f6f36976`
  - 服务端确认 **online:1**，心跳正常
  - 从设备导入 Agent 接口实测通过（列出 4 个 Agent）
- **打包命令**（Windows / 40 机）：
  ```
  cd C:\Users\Chee
  pyinstaller --clean --noconfirm --distpath C:\Users\Chee\dist --workpath C:\Users\Chee\build C:\Users\Chee\sidecar\scripts\build.spec
  ```
  - spec：`sidecar/scripts/build.spec`（内嵌 config/sidecar.json + lib/）
  - 产物：`dist\sidecar.exe`（~9.8MB）
- **配置机制**：服务用 `%APPDATA%\CherryManaged\config.json`（40机在 systemprofile 下），换 exe 不影响配置；首次无用户配置时用内嵌模板生成落盘。

### 打包环境（40 机, DESKTOP-RFR42OQ）
- Windows + Python 3.11.9 + pyinstaller 6.22.2 + websocket-client 1.9.0
- 源码：`C:\Users\Chee\sidecar\`（已与本机 `73ce80b5...` md5 同步）

---

## 六、验证记录（本轮实测全绿）

| 项 | 结果 |
|----|------|
| export 导出包 | 633B，含完整 metadata+agent ✅ |
| import 上传（带 schema 壳） | HTTP 201 落库 rev1 ✅ |
| import 同名冲突 | HTTP 409 ✅ |
| delete 清理 | HTTP 200 ✅ |
| 管理后台页面 | HTTP 200，含导出按钮+文件导入卡 ✅ |
| JS 语法 | node --check 通过 ✅ |
| 40 机新 exe 部署 | 服务 RUNNING，online:1 ✅ |
| 从设备导入 Agent | HTTP 200，列出 4 个 Agent ✅ |

---

## 七、遗留 / 下一步（未做）

- [ ] 真浏览器（playwright）点一遍 UI 上传导入（本环境无 headless，已用 node fetch 覆盖同 API 路径）
- [ ] /tmp 测试脚本 + 40 机遗留测试包清理（`ui-test-agent`/`ui-import-test`/`e2e-pkg-no-id`/`test-agent-001`）
- [ ] CherryHQ 上游 v2.0.4/2.0.5 是否合并（managed 相对 upstream 落后 127 commit，未擅自合）
