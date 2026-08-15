# 批次H：Sidecar 服务端地址自动发现与安装配置（B+A+E+F）方案

> 日期：2026-08-15 · 状态：定稿 · 关联：CherryStudio 企业受管版 v4.0 E-4
> 老板拍板（2026-08-15 18:42）：B+A+E+F 四合一落地，服务端先定 181（HOST_181）

## 〇、背景与要解决的问题

当前 sidecar 内嵌配置 `server.url = "ws://127.0.0.1:2334/ws"`（写死本机回环），
且 `_load_config` 只有 `--config > 用户级 config > 内嵌模板` 三个来源，**无任何自动发现能力**。

**后果**：每装一台新员工机，都要手动改 `%PROGRAMDATA%\CherryManaged\config.json` 的
`server.url` 指向公司服务端，否则连本机（本机无服务端 → 连空）。**违背 v4.0 E-4 "开箱即用、员工零配置"核心目标**。

已排除方案（老板决策）：
- **D（DNS SRV）**：公司无内部 DNS，无法落地 ❌
- **C（纯 UDP 广播自动首连）**：无人值守自动连有"连错/被冒充"风险，不采用 ❌
- **采用：B+A+E+F 四合一**（用户主动触发 + 展示确认，规避广播风险）

## 一、需求定稿（老板拍板描述）

### B+A：安装时 IP 确认弹窗（默认 + 可改）
安装软件时弹一个窗口，**默认填好服务端地址（HOST_181）**，可直接**手动改成别的 IP**，
确认后即装好即用、之后不用再配。

### E：弹窗内"扫描局域网"按钮
安装弹窗里有个**按钮**，点击后**扫描当前局域网中运行的受管服务端 IP**，自动定位并填入 IP 地址，
用户确认后再点确认。

### F：安装目录配置工具（可重配）
除安装时触发外，**安装包中要有一个脚本/工具**，装完使用中如果**服务端 IP 变了**，可**重新触发一次**（重扫或手动改），改完即生效。

### 服务端地址基准
- 服务端先部署在 **181（HOST_181）**：`uvicorn main:app --host 0.0.0.0 --port 2334`

## 二、交互与安全设计（为什么这样安全）

广播风险（连错/被冒充）在**无人值守自动连**时才致命。B+A+E 全部是**用户主动触发 + 肉眼确认**：
- 用户**点击"扫描"**才广播（不是后台自动）→ 无持续暴露
- 扫描结果**列给用户看**，用户看到 IP 自己选 → 天然防"连错服务端"
- 用户看到陌生/恶意 IP 不会点确认 → 无被劫持窗口

F 同理：装完服务端 IP 变了，用户**主动**跑配置工具重扫/改，改完确认。

## 三、技术设计

### 3.1 E 扫描协议（sidecar ↔ 服务端局域网发现）

**方式**：UDP 单次广播查询 + 单播回应（服务端可选组播）。
- **服务端**（server/）监听一个 UDP 发现端口（如 **2335**），收到"发现请求"后，校验请求内嵌的共享 token（对齐现有 WS token），校验通过则单播回应自身 IP + 端口 + 版本。
- **请求方**（sidecar 新增 `discover` 命令 / NSIS 弹窗调它）：向 `255.255.255.255:2335` 发一条 UDP 广播 `{"type":"cherry-managed-discovery","token":"<共享token>"}`，等待 1-2s 收集回应，列出候选。

**为什么 E 用主动广播也可以**：因为结果**上屏给用户确认**，不是自动连。用户看到扫到的 IP 才点确定。

**响应格式**（服务端回应）：
```json
{
  "type": "cherry-managed-discovery-ack",
  "server_ip": "HOST_181",
  "port": 2334,
  "version": "4.0.0-rc.1",
  "build": "batchH"
}
```

### 3.2 config 改动（sidecar 内嵌模板 + 用户级）

- `sidecar/config/sidecar.json`：
  - `server.url` 默认改为 `ws://HOST_181:2334/ws`（服务端地址基准 181）
  - 新增 `discovery: { port: 2335, timeout_ms: 2000, enabled: true }`
- `_load_config` 逻辑**不变**（仍是 `--config > 用户级 > 内嵌`），内嵌默认地址即公司服务端。

### 3.3 sidecar 新增命令

`sidecar.exe discover`：
- 发 UDP 广播找服务端，收集回应，**打印候选列表**（每行一个 IP:port）
- 供 NSIS 弹窗和 F 工具调用
- `sidecar.exe set-server <ip> [port]`：
  - 把 `server.url` 写进**用户级 config**（`%PROGRAMDATA%\CherryManaged\config.json`），覆盖内嵌默认
  - F 工具改 IP 就调它

### 3.4 NSIS 安装弹窗（B+A+E）

在 `nsis-installer.nsh` 的 `customInstall` 增加一个**自定义 Page**：
- 默认编辑框填 `HOST_181`（内嵌基准）
- "扫描局域网"按钮 → 后台调 `sidecar.exe discover` → 把第一个/列表结果填入编辑框
- 用户可手动改 → 点"下一步/确认" → 把用户填的地址写进用户级 config(`set-server`) → 继续原 first-run 流程
- 校验：地址非空、格式合法（IP 或 host），非法则报错留在本页

> ⚠️ NSIS 自定义 Page 回调（`Page custom xxxPre xxxLeave`），扫描按钮用 `nsExec::ExecToLog` 或 `ExecDos` 调 discover，读 stdout 填框。

### 3.5 F 配置工具

安装目录放 `配置CherryStudio服务端.bat`（或小 exe）：
- 双击 → 提示输入/扫描服务端 IP（复用 discover + set-server 逻辑）
- 改完调用 `sidecar.exe set-server <ip>` 写 config + 提示重启服务 `sc.exe stop/start CherrySidecar`
- 与 NSIS 安装时生成的 `启动CherryStudio服务.bat` 并列，都放 `$INSTDIR\`
- 卸载时清理（对齐 customUnInstall）

## 四、验收标准

| ID | 动作 | 通过条件 |
|----|------|---------|
| AC-H-1 | 安装弹窗默认显示 HOST_181 | 默认值正确，可手动修改 |
| AC-H-2 | 点"扫描局域网" | 扫到 181 服务端，IP 自动填入 |
| AC-H-3 | 改 IP 后确认 | 用户级 config 的 server.url 变为所填地址 |
| AC-H-4 | 装完即连 | sidecar 连新地址，服务端 devices 表 online=1 |
| AC-H-5 | F 工具改 IP | config 更新 + 服务重启后连新地址 |
| AC-H-6 | 非法 IP 输入 | 弹窗报错，不允许通过 |

## 五、服务端新增（discovery 端口）

- `server/main.py` 或新增 `server/discovery.py`：UDP socket 监听 `0.0.0.0:2335`，收到带正确 token 的发现请求 → 单播回应自身地址。
- 不依赖 WS，独立于 2334 主服务；服务端起时一并监听。

## 六、打包与集成

- `fork-win-build.yml`：sidecar.exe 已含 discover/set-server 命令（同一二进制）；F 的 bat 随 NSIS 生成（不改 extraResources）
- `electron-builder.yml`：不改（F 工具走 NSIS 生成，非 extraResources）

## 七、风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| 广播同子网才通 | 🟡 中 | 公司员工机同 INTERNAL_NETWORK/24 主网段；跨网段用 B 手动改兜底 |
| 扫到无关 UDP 服务 | 🟢 低 | 回应带共享 token 校验 + 特定 type 字段，不会误认 |
| 恶意节点冒充 | 🟢 低 | token 校验 + 结果上屏用户确认（非自动连） |
| 服务端与磁盘目录不一致 | 🟢 低 | discovery 端口 2335 独立于主服务，不起则扫不到 |

## 八、实施顺序

1. 服务端加 discovery 端口（server/discovery.py）→ 181 起
2. sidecar 加 discover/set-server 命令
3. NSIS 弹窗（B+A+E）
4. F 配置工具 bat
5. 打包验证 + 真机测（94/181）

> 本文档对齐 v4.0 E-4"开箱即用、员工零配置"。服务端基准地址 181 为临时定址，后续可改（B+F 支持随时重配）。
