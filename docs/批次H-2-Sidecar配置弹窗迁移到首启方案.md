# 批次H-2 · 服务端地址配置弹窗迁移：NSIS 安装时 → CherryStudio 首启时

## 背景与动机

批次H 原方案在 **NSIS 安装时**弹出"配置服务端地址"窗体（调 `configure-server.ps1 -Auto`）。
老板实测装机后**没有弹窗**，定位根因：ps1 `-Auto` 短路逻辑——
只要 `%PROGRAMDATA%\CherryManaged\config.json` 存在且 `server.url` 非空就 `exit 0`，
而该 "非空" 正则 `[^/]+` 几乎匹配一切，加上历史装机遗留的 config.json，
导致首次安装时弹窗被静默跳过。

老板提出更稳方案并批准：**把弹窗从 NSIS 安装器迁移到 CherryStudio 应用首次运行时**。

## 为什么首启弹窗更优（老板判断正确）

| 维度 | NSIS 安装时（原） | 应用首启时（新） |
|------|------------------|------------------|
| 稳定性 | 安装器上下文调 PowerShell WinForms 不稳定，可能被抑制/一闪而过 | CherryStudio 是正常 GUI 进程，BrowserWindow 弹窗稳定 |
| 判断依据 | 靠猜测 config.json，重装/遗留误判 | 应用真正要连服务端那一刻判断，准确 |
| 权限/焦点 | 管理员态 + UAC + 杀软干扰 | 用户态正常上下文 |
| 升级/重装 | 需处理重复弹 | 已配置过自动跳过，天然幂等 |

## 实现方案

### 迁移方向：NSIS 移除弹窗；app 首个配置窗

1. **NSIS（nsis-installer.nsh）**：移除 `customInstall` 中调用 `configure-server.ps1 -Auto` 的弹窗段。
   保留：写 CHERRY_MANAGED_BUILD 标记、`sidecar.exe first-run`、生成 `启动CherryStudio服务.bat` 兜底、LANSetup 防火墙。
   `configure-server.ps1` 保留作为 **F 配置工具**（装完可重跑），不再由安装器自动调用。

2. **app 侧新增 SidecarConfig 配置窗**（学 MigrationWindowManager 独立窗口范式）：
   - `src/renderer/windows/sidecarConfig/`：index.html + entryPoint.tsx + SidecarConfigApp.tsx
     - 表单：服务端 IP:端口（默认 HOST_181:2334）+ "扫描局域网"按钮 + 候选列表 + 确认/取消
   - `electron.vite.config.ts` renderer `rollupOptions.input` 增加 `sidecarConfig` 入口
   - 新增 `WindowType.SidecarConfig` + `WINDOW_TYPE_REGISTRY` 注册
   - main 侧 `SidecarConfigWindowManager`（独立 BrowserWindow，学 MigrationWindowManager，不走 WindowManager 主系统以隔离风险）
   - IPC 通道（`/shared/IpcChannel.ts` 扩展 + preload `window.api` + main `ipcMain.handle`）：
     - `SidecarConfig_GetServerUrl` → 读 `%PROGRAMDATA%\CherryManaged\config.json` 的 `server.url`
     - `SidecarConfig_ScanLan` → 调 `sidecar.exe discover`（输出 `IP:port` 行）
     - `SidecarConfig_SetServer` → 调 `sidecar.exe set-server --ip X --port Y`
     - `SidecarConfig_Close` → 关闭窗口

3. **ManagedSidecarService 首启检测**：
   - `onReady()` 中，受管版 + `isWin` 且 `sidecar.exe` 存在时：
     - 读 config.json 的 `server.url`：
       - **有有效地址** → 走原服务自愈逻辑（查 NSSM 服务，未注册则 first-run）
       - **无地址 / 未配置** → 打开 SidecarConfig 配置窗，让用户填；保存后回写 config，再走服务自愈
   - 弹窗不阻塞启动（fire-and-forget，窗口打开后主流程继续）

### 读取/写入 config.json（对齐 sidecar.py）

- 路径：`%PROGRAMDATA%\CherryManaged\config.json`
- 读：`server.url` 形如 `ws://IP:PORT/ws`，非空即有配置
- 写：`sidecar.exe set-server --ip <IP> --port <PORT>`（复用现成命令，不自己写文件）

## 验收

1. 安装新包后首次启动，若未配置服务端地址 → 弹 SidecarConfig 窗
2. 填地址/扫描 → 保存 → config.json `server.url` 更新
3. 重启后不再弹（已配置）
4. NSIS 安装过程不再弹窗，但仍完成标记 + first-run + bat 兜底
5. 官方版（无 sidecar / 无 CHERRY_MANAGED_BUILD）完全不触发

## 遗留

- `configure-server.ps1`（F 工具）保留，作为手动改地址兜底；但不从安装器自动调
