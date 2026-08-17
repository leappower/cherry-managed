##
# CherryStudio Agent 远程管理经验存档
##
# 路径: Y:\Chee\OpenClaw_C\cherry-managed\
# 本文: SKILL经验蒸馏.md
# 最后更新: 2026-07-18
##

##
# 一、SKILL 分发机制
##

## 1.1 架构
# URL: http://HOST_175:5005/WebDAV_Data/skills/<名称>.zip
# 认证: basic auth (chee:Aa123123)
# 来源 (Mac/NAS): /Volumes/Chee_2/OpenClaw/Skills/MLL_API/dist/
# 打包: zip -j <名称>.zip scripts/*.py scripts/model-pool.json

## 1.2 Agent 下载方式（System Prompt 内嵌）
# ```bash
# curl -sL -u "chee:Aa123123" "http://HOST_175:5005/WebDAV_Data/skills/<名称>.zip" -o skill.zip
# && tar -xf skill.zip && del skill.zip
# ```

## 1.3 System Prompt 双路径检测
# Agent 的 Bash 工具默认 CWD = Agent 工作目录（accessible_paths）
# 下载 SKILL 后解压可能在 CWD 或当前目录
# 所以脚本读 .env 必须同时扫描 CWD 和脚本所在目录

##
# 二、Agent 的 Key 管理
##

## 2.1 Key 存放方案演进

# ❌ 方案 A: PATCH API 写入 env_vars（2026-07-18 16:07）
#   Agent 执行 curl localhost:23333 调 API
#   问题：Agent 没有本机 CherryStudio API Key，认证失败
#   结论：❌ 不可行

# ❌ 方案 B: env_vars 注入（2026-07-18 16:03）
#   Mac 端直接 PATCH 写入 Kuai Key 到 env_vars
#   问题：我们的 Key 暴露在小陈电脑上
#   结论：❌ 安全不合规

# ✅ 方案 C: .env 文件（2026-07-18 16:30）
#   Agent 收到用户 Key → echo MLL_KUAI_KEY=*** > .env
#   脚本读 .env → load_kuai_key 生效
#   问题：Windows 不支持 .env 文件名开头的小数点（文件管理器不显示）
#   结论：✅ 方案正确，但需注意路径

## 2.2 当前方案细节
# mll-review.py load_kuai_key() 搜索 .env 顺序：
#   1. 环境变量 MLL_KUAI_KEY（最高优先级）
#   2. 当前工作目录 .env（Path.cwd() / ".env"）
#   3. 脚本所在目录 .env（os.path.dirname(__file__) / ".env"）
#   三个都不存在 → 报错提示用户配置

# System Prompt 中的 KEY 初始化流程：
#   1. dir .env → 文件不存在 → 向用户要 Key
#   2. echo MLL_KUAI_KEY=*** > .env ← 写入 CWD
#   3. python3 mll-review.py pool-status ← 验证（脚本从 CWD 或脚本目录读 .env）

##
# 三、Agent 行为控制经验
##

## 3.1 问题：Agent 跳过程序不执行自检
# 表现：用户输入问题后，Agent 直接回答，不检查文件是否存在、Key 是否配置
# 根因：DeepSeek V4 Flash 对 instructions 的遵循度有限
#   "请先做 A 再做 B" → Agent 视为参考建议，不强制执行
# 修复方向：
#   - 将检测步骤改为"铁则"而非"流程"：
#     "任何时候收到用户消息，先做三件事（按此顺序），再回答用户问题"
#   - 给出示例流程
#   - 极短指令 + 明确的前置依赖

## 3.2 已知的 Agent 行为陷阱

# 陷阱1: 下载后需要在 CWD 中解压，不可写到其他目录
#   修复: tar -xf skill.zip 不带路径（解压到 CWD）

# 陷阱2: 脚本路径 = 解压目录 ≠ CWD
#   修复: .env 扫描双路径（CWD + 脚本目录）

# 陷阱3: Windows echo 对 Key 中的特殊符号没问题（*? 在 echo 中纯文本输出）
#   但文件名 .env 在 Windows 下被视为"无扩展名文件"，dir .env 可查

# 陷阱4: Agent 在 accessible_paths 目录外写入 .env 可能被权限拦截
#   修复: 确保 System Prompt 里只写本地目录

# 陷阱5: PowerShell 和 CMD 的 echo 语法不同
#   CMD: echo KEY=VALUE > .env ✅
#   PowerShell: echo KEY=VALUE > .env 也 ✅（但编码可能不同）

##
# 四、mll-review SKILL 版本记录
##

## 4.1 GitHub 仓库
# 仓库: https://github.com/leappower/openclaw-skills
# 目录: skills/mll-review/
# 文件: SKILL.md + scripts/mll-review.py（+ model-pool-manager.py 共享）

# v1.0 (bced475) — 从 mll-api 裁剪评审版
#   - 保留：review / questioning / fusion / chat / pool-update / pool-status
#   - 删除：generate / vision / debug_log
#   - 删除：_get_image_models / _get_vision_models / _pick_default_image
#   - 删除：IMAGE_MODELS / VISION_MODELS / _IMAGE_MODELS_FALLBACK

# v1.1 (5a2d689) — 移除 secrets 引用 + --key 参数
#   - load_kuai_key 不再读 secrets.json / API-KEYS.md
#   - 新增 --key 参数到每个子命令

# v1.2 (1d6d34c) — 新增 .env 文件读取
#   - load_kuai_key 新增本地 .env 文件作为 Key 来源
#   - Key 由 Agent 自行写入 .env，不依赖 API PATCH

# v1.3 (fe12e8a) — 双路径扫描 .env
#   - .env 扫描路径：CWD + 脚本目录
#   - 解决 Agent CWD 和脚本解压目录不同的 bug

## 4.2 NAS 分发
# 地址: http://HOST_175:5005/WebDAV_Data/skills/skill-review.zip
# 打包命令:
#   cd ~/.openclaw/skills/mll-review
#   zip -j /tmp/skill-review.zip scripts/mll-review.py scripts/model-pool-manager.py scripts/model-pool.json
#   curl -s -u "chee:Aa123123" -T /tmp/skill-review.zip "http://HOST_175:5005/WebDAV_Data/skills/skill-review.zip"

##
# 五、Agent 创建/更新流程
##

# 1. 获取目标机器的 CherryStudio API Key
#    python3 cs-key.py get <hostname>     ← 推荐
#    或 直接读 list.json

# 2. 列出已有 Agent
#    curl -s http://<IP>:23333/v1/agents -H "Authorization: Bearer <Key>"

# 3. PATCH 更新 Agent（推荐用于已有 Agent）
#    curl -s -X PATCH http://<IP>:23333/v1/agents/<agentId> \
#      -H "Authorization: Bearer <Key>" \
#      -H "Content-Type: application/json" \
#      -d '{"instructions": "System Prompt内容", "configuration": {...}}'

# 4. 创建新 Agent（注意 accessible_paths 必填）
#    curl -s -X POST http://<IP>:23333/v1/agents \
#      -H "Authorization: Bearer <Key>" \
#      -H "Content-Type: application/json" \
#      -d '{"type": "claude-code", "name": "...", "accessible_paths": ["D:\\..."]}'

# ⚠️ 注意事项
#   - Python 传 Key 必须用文件写入再 curl（bash shell 会吞特殊字符）
#   - 建议用 python3 subprocess.run(['curl', ...]) 避免 shell 转义
#   - PATCH 时 body 用 json.dumps() 确保准确
#   - permission_mode 设为 bypassPermissions 否则 Agent 每步都要问用户
#   - 小陈的 Key（CS_SK_KEY_CHEN）和梁酱的 Key（CS_SK_KEY_LIANG）各自独立
#   - Kuai API Key 通用（所有机器共用同一个 Kuai Key）

##
# 六、覆盖的 SKILL 清单
##

# mll-review (评审工坊)
#   - load_kuai_key: --key > env > .env(双路径)
#   - GitHub: skills/mll-review/
#   - NAS: skill-review.zip
#   - System Prompt: templates/system-prompt-mll-review.txt
#   - 部署机器: 小陈 Windows（🧠 MLL 评审工坊）
#
# mll-client (图片工坊)
#   - load_kuai_key: --key > env > .env(双路径)
#   - GitHub: skills/mll-client/
#   - NAS: img-gen/skill.zip
#   - System Prompt: templates/system-prompt-mll-client.txt
#   - 部署机器: 小陈 Windows + 梁酱 Windows（🎨 MLL 图片工坊）
#
# mll-engine (全功能版，含评审+生图+视觉)
#   - 不在本蒸馏范围，是内部版

##
# 七、受管版 Windows 安装包打包/部署核心坑与结论（2026-08-17 最终闭环）
##
# 目标：受管版 CherryStudio 装任何 Windows 机"安装即用"——首启弹配置窗、
#       sidecar 服务自动注册/自愈、局域网直连，全程无需手动配置。

## 7.1 受管判定：进程 env 继承坑（最隐蔽，曾让所有受管行为静默失效）
# ❌ 原：NSIS `WriteRegStr HKCU Environment CHERRY_MANAGED_BUILD 1` 只写注册表环境变量，
#      但 app 运行时读 `process.env.CHERRY_MANAGED_BUILD`。注册表 env 不进入当前进程树
#      （只对下次登录新进程生效）→ isManagedBuild() 永远 false → 首启弹窗/服务自愈/
#      局域网监听覆盖全部静默跳过。94 号机早期"局域网通"其实是安装器 portproxy 做的
#      假象，受管运行时自检从未真正跑起来过。
# ✅ 结论：改为运行时检测安装目录 `resources\sidecar\sidecar.exe` 是否存在
#      （ManagedSidecarService.ts isManagedBuild()，受管版必有、官方版必无），
#      不再依赖进程 env 继承时序。process.env 仅保留作测试 fast-override。
#      实操：写在 cherry-src src/main/services/ManagedSidecarService.ts。

## 7.2 用户配置目录：%PROGRAMDATA% 权限坑（实测"写入服务端地址失败"）
# ❌ 原：_user_config_dir() Windows 用 %PROGRAMDATA%\CherryManaged（全局受保护目录）。
#      运行时普通权限进程（Electron spawn sidecar.exe set-server）写它 → PermissionError
#      → 配置窗保存报"写入服务端地址失败，请检查权限后重试"。首启能写是因为 NSIS 安装
#      时管理员跑 first-run；但普通权限保存必失败。
# ✅ 结论：改存 %APPDATA%\CherryManaged（Roaming，普通用户可写）。sidecar/sidecar.py
#      _user_config_dir()。卸载时 NSIS 显式 `RMDir /r "$APPDATA\CherryManaged"` 实现
#      "重装必重选服务端"。多用户每用户独立。无权限隐患、业界标准。

## 7.3 PS5.1 中文乱码根因：打包丢了 UTF-8 BOM
# ❌ 现象：configure-server.ps1 装的机上中文全乱（"受管版"→"鍙楃鐗?"）+ 语法错"方法调用缺少 )"。
# ✅ 根因：源文件有 UTF-8 BOM (ef bb bf)，但打包产物无 BOM (23 20 3D 纯ASCII)，
#      PS5.1 把 UTF-8 中文按 ANSI/GBK 解析 → 乱码 + 把中文当代码报错。
# ✅ 结论：electron-builder extraResources 二进制复制本不剥 BOM（Node copyFileSync 验证
#      保留 efbbbf），是历史产物缺 BOM。根治：scripts/after-pack.js Windows 分支强制
#      二进制读改写回 EF BB BF（幂等，有则跳过）。PS 脚本另注意 if($result -eq "OK")
#      DialogResult 枚举 vs 字符串比较在 PS5.1 不可靠。

## 7.4 PowerShell `sc` 是 Set-Content 别名坑
# ❌ 老板在 PowerShell 输 `sc query CherrySidecar` 显示空 → 误判服务未注册。
#     实为 `sc` 是 Set-Content 别名，写了个叫 query 的文件。
# ✅ 结论：查服务用 `Get-Service CherrySidecar` 或 `sc.exe query`（带 .exe 绕过别名）。
#     服务早就注册 Running。auto-heal 在 Node execFile('sc') 走真 sc.exe 所以判断正确。

## 7.5 electron-builder extraResources 二进制 vs 文件夹坑
# ❌ `to: "sidecar"` 把 Windows 10MB 二进制 DLL 打成无扩展名 FILE resources\sidecar，
#     而非文件夹；NSIS 找 resources\sidecar\sidecar.exe（带 .exe）找不到 → 注册表标记/
#     NSSM 服务/首启全不触发。
# ✅ 结论：`to` 必须带完整文件名+扩展名，如 `to: "sidecar/sidecar.exe"`、`to: "sidecar/nssm.exe"`。
#     nssm.exe 由 CI pre-place 下载后经 extraResources 注入正确资源路径，
#     不要放仓库 resources/sidecar/（会被 electron-builder 吸进 app.asar.unpacked）。

## 7.6 NSSM 服务注册 AppExit 语法坑
# ❌ sidecar.py nssm AppExit Restart 缺 Default → exit 1 注册失败。
# ✅ 结论：用 `AppExit Default Restart`；子进程调用包 _run() 包装 + gbk decode errors="replace"。

## 7.7 平台不可构建约束 + 交付验证纪律
# ✅ Linux(181) 不能构建 Windows 安装包（electron-builder 缺 win32-x64 预编译
#      @img/sharp 等 assertPrebuiltPackages 硬失败）→ 必须在 CI(GitHub Actions Windows) 构建。
# ✅ 交付前纪律：先 `npx electron-builder --dir` Linux 本地 dry-run 抓语法错全绿再
#      push 触发 Windows CI；交付前 7zz 解包验证关键文件路径；一个 bug 只允许犯一次，
#      验证成本不能转嫁给老板反复装机测试。
# ✅ push 私有仓库安全姿势：token 提自 QAIMarketingSystem/.git/config，用 Python 脚本
#      拼 URL（避免内联 shell 被显示层把 token 抹成 ***），输出自动脱敏。

## 7.8 受管版部署验证清单（40号机实测全绿 2026-08-17）
# 1. 扫描局域网+保存服务端地址 ✅（7.2 修复后不再报写入失败）
# 2. CherrySidecar 服务 Running ✅（Get-Service 查）
# 3. 局域网直连 http://<ip>:23333 ✅
# 4. 卸载重装重新弹服务端选择 ✅（7.2 + NSIS 删 APPDATA 目录）
