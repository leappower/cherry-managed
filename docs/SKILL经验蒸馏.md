##
# CherryStudio Agent 远程管理经验存档
##
# 路径: Y:\Chee\OpenClaw_C\CherryStudioAgent远程控制\
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
