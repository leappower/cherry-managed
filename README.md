# CherryStudio 企业受管版

公司内部 CherryStudio 企业受管分发系统：统一派发模型/Agent/SKILLS/MCP，员工端受管项锁死，数据采集驱动进化，升级免疫。

## 项目结构

```
CherryStudioAgent远程控制/
├── docs/
│   ├── 方案-企业受管版-v1.0.md     ← 完整方案（R1-R7 + 架构 + 交付）
│   ├── INDEX.md                   ← 原管理中枢入口
│   └── SKILL经验蒸馏.md            ← 历史经验存档
├── server/                        ← 管理服务端（待建）
├── sidecar/                       ← 员工端常驻进程（待建）
├── fork-patches/                  ← CherryStudio Fork 改动（待建）
├── gateway/                       ← 模型网关·Key保护（可选·待建）
├── templates/                     ← Agent/端口转发模板（已有）
├── cs-key.py                      ← Key 映射脚本（已有）
└── list.json                      ← 机器清单（已有，含敏感 Key，不入库）
```

## 核心思路

| 层 | 职责 | 技术 |
|----|------|------|
| **服务端** | 模型/Agent/SKILLS 仓库 + 派发 + 数据接收 | Python (FastAPI) |
| **Sidecar** | 员工端受管进程：同步/采集/校验/对接 | Python |
| **Fork** | CherryStudio UI 锁死 + 更新接管 | CherryStudio 源码 |

## 文档
- [完整方案](docs/方案-企业受管版-v1.0.md)

## 安全说明
- `list.json` 含 API Key，已被 `.gitignore` 排除，**禁止提交**
- Key 通过 `cs-key.py` 映射获取，AI 不直接读 `list.json`
