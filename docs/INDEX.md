##
# CherryStudio Agent 远程管理规范
##
##
# 路径: Y:\Chee\OpenClaw_C\cherry-managed\
# 最后更新: 2026-07-18 15:36
# 管理中枢: OpenClaw (鮱澄 @ Mac HOST_180)
# 适用: 局域网 CherryStudio API 远程管理
##

##
# 一、目录结构
##
#
# cherry-managed/
# ├── INDEX.md               ← 本文件（唯一入口）
# ├── cs-key.py              ← Key 映射脚本（唯一获取 Key 的方式）
# ├── list.json              ← 机器/Key 清单
# └── templates/             ← 模板文件
#     ├── agent-create.json          ← 创建 Agent 模板
#     ├── agent-patch.json            ← 更新 Agent 模板
#     ├── system-prompt-mll.txt       ← MLL 图片工坊 System Prompt
#     ├── chery_api_lan_23333.bat    ← Windows 端口转发脚本
#     └── mac-lan-socat.sh           ← Mac 端口转发脚本
##

##
# 二、机器清单（list.json）
##
#
# 格式：
# {
#   "version": "1.0.0",
#   "updated": "ISO-8601",
#   "machines": [
#     {
#       "hostname": "HOST_CHEN",
#       "alias": "小陈 Windows",
#       "owner": "小陈",
#       "ip": "HOST_188",
#       "port": 23333,
#       "api_key": "cs-sk-完整key不脱敏",
#       "os": "windows",
#       "status": "active"
#     }
#   ]
# }
#
# 铁则：
# - api_key 必须完整写入，不缩写不脱敏
# - hostname 唯一，用于 cs-key.py 索引
# - 新增机器：加一个条目即可，Key 不要混用
#

##
# 三、Key 映射规则（核心铁律）
##
#
# ❌ 禁止：大模型（AI）直接读取 list.json 的 api_key 字段
# ✅ 必须：通过 cs-key.py 间接获取
#
# 原因：AI 会缩写 Key（c81…52f6），导致 403 认证失败
#
# 使用方法：
#   python3 cs-key.py list                  # 列出所有机器
#   python3 cs-key.py get <hostname>        # 获取指定机器的 Key
#   python3 cs-key.py api <hostname> <path> # GET 请求
#   python3 cs-key.py curl <hostname> <args> # 自定义 curl
#
#  cs-key.py 会自动：
#   1. 从 list.json 读取完整的 Key
#   2. 拼接目标 URL（http://<ip>:<port>）
#   3. 加入 Authorization: Bearer 头
#   4. 执行请求返回结果
#

##
# 四、CherryStudio API 端点
##
#
# 所有电脑 API 端口统一为 23333
# 认证: Authorization: Bearer <Key>
#
# 路径        方法  用途
# /health      GET   健康检查
# /v1/agents   GET   列出 Agent
# /v1/agents   POST  创建 Agent
# /v1/agents/{id}    GET    查看单个
# /v1/agents/{id}    PATCH  部分更新
# /v1/agents/{id}    PUT    全量更新
# /v1/agents/{id}    DELETE 删除
# /v1/agents/{id}/sessions              POST 创建会话
# /v1/agents/{id}/sessions/{sid}/messages POST 发送消息
#

##
# 五、操作流程
##

## 5.1 新增一台 Windows 电脑
#
# 1. 目标电脑安装 CherryStudio → 设置 → API Server → 开启 → 复制 Key
# 2. 管理员运行 chery_api_lan_23333.bat → 选 1 Turn ON
# 3. list.json 中新增 machines 条目，填入 ip/api_key/owner
# 4. 创建 Agent:
#    python3 cs-key.py post <hostname> /v1/agents '{"type":"claude-code","name":"🎨 MLL 图片工坊","model":"deepseek:deepseek-v4-flash","configuration":{"permission_mode":"bypassPermissions","max_turns":100}}'
# 5. 记录返回的 agentId
# 6. 推送 System Prompt:
#    python3 cs-key.py patch <hostname> /v1/agents/<agentId> '{"instructions":"$(cat templates/system-prompt-mll.txt)"}'

## 5.2 更新 Agent 指令
#
# python3 cs-key.py patch <hostname> /v1/agents/<agentId> '{"instructions":"新内容"}'
# 替换 alias: HOST_CHEN | HOST_LIANG

## 5.3 更新 Skill（NAS 分发）
#
# 1. 编辑脚本文件
# 2. 打包: zip skill.zip mll-engine.py mll-client.py version.json
# 3. 上传 NAS: curl -u "chee:Aa123123" -T skill.zip "http://HOST_175:5005/WebDAV_Data/skills/img-gen/skill.zip"
# 4. 推送 GitHub: git add && git commit && git push
# 5. 目标电脑新建会话 → 自动下载新版

## 5.4 Key 更新
#
# 1. 目标电脑 CherryStudio 重启后 Key 可能重新生成
# 2. 打开设置 → API Server → 复制新 Key
# 3. 更新 list.json 中对应机器的 api_key
# 4. 不需要改其他内容

##
# 六、当前部署状态
##

# 主机名         名称          IP:端口            系统     状态     Agent
# HOST_CHEN   小陈 Windows  HOST_188:23333 Win11    active  🎨 MLL 图片工坊
# HOST_LIANG  梁酱 Windows  HOST_69:23333  Win11    active  🎨 MLL 图片工坊

##
# 七、常见问题
##

# Q: Key 正确但 403
# A: 检查机器名是否正确（cs-key.py api HOST_CHEN 和 HOST_LIANG 不同）
#    检查前面是否有空格（Key 必须精确匹配，无空格）

# Q: Key 正确但 401
# A: 没有带 Authorization header

# Q: Key 被截断
# A: 大模型直接读 list.json 会缩写 Key → 必须通过 cs-key.py 映射

# Q: 更新了 NAS 但 Agent 还是旧版
# A: 只有新建会话才下载，已有会话不刷新

# Q: 远程发消息看不到前端 UI
# A: API 消息只走 SSE，不回写 CherryStudio 聊天窗口

# Q: 端口转发不起效
# A: templates/chery_api_lan_23333.bat 管理员运行选 1
#    公共网络需改为专用网络

##
# 八、路径映射
##

# Windows                macOS
# Y:\Chee\OpenClaw_C\    /Volumes/Chee_2/Chee/OpenClaw_C/
# Y:\OpenClaw\           /Volumes/Chee_2/OpenClaw/
# Y:\OpenClaw\CherryStudio\        /Volumes/Chee_2/OpenClaw/CherryStudio/
