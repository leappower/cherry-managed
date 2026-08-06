---
name: mll-review
description: "多模型评审引擎(客户版): review/questioning/fusion/chat/pool。不含KEY，需自行配置MLL_KUAI_KEY。"
version: "1.0"
---

# MLL Review v1.0 — 多模型评审引擎（客户/Agent 版）

> 从 mll-api 抽取的评审版。不含 Key，用户需自行配置。
> 专注：交叉评审、反向追问、博弈融合、单模型对话、模型池管理。

## 用途

通过 Kuai API 调用多厂商模型进行评审分析和对话。不包含生图和视觉。

## 前置条件

```bash
# 方式一：环境变量（推荐）
export MLL_KUAI_KEY=***

# 方式二：每次命令传入
python3 mll-review.py review --key *** --prompt "..."
```

## 命令一览

| 命令 | 用途 | 适用场景 |
|------|------|---------|
| `review` | 交叉评审 | 多模型评审方案/代码/文案 |
| `questioning` | 反向追问 | 挖掘需求盲区 |
| `fusion` | 博弈融合 | 多模型输出融合为一份报告 |
| `chat` | 单模型对话 | 快速问答、提示词优化 |
| `pool-update` | 更新模型池 | 拉取 Kuai 最新模型 |
| `pool-status` | 查看模型池 | 各类型模型分布 |

## 功能详解

### 1. 交叉评审（review）

多模型从不同角色视角评审内容，输出评分+意见报告。

```bash
# standard 级别（默认，6-10 模型）
python3 mll-review.py review --prompt "评审这段文案的优缺点" -o review.md

# deep 级别（10-15 模型，更全面）
python3 mll-review.py review --level deep --file 方案.md -o review.md

# quick 级别（4-6 模型，快速）
python3 mll-review.py review --level quick --prompt "快速评审这段代码"
```

参数：
| 参数 | 说明 | 默认 |
|------|------|------|
| `--level` | 评审级别：quick/standard/deep | standard |
| `--prompt` | 待评审内容 | — |
| `--file` | 从文件读取内容 | — |
| `--output` / `-o` | 输出文件路径 | /tmp/mll_review_xxx.md |
| `--dry-run` | 试运行（只选模型不执行） | — |
| `--skip-probe` | 跳过嗅探（使用静态模型列表） | — |

### 2. 反向追问（questioning）

多模型从不同角度的挖掘需求盲区。

```bash
# quick 级别（2-4 模型）
python3 mll-review.py questioning --level quick --prompt "我要做一个电商平台"

# standard 级别（4-6 模型）
python3 mll-review.py questioning --prompt "设计一款智能咖啡机" -o questions.md
```

参数：
| 参数 | 说明 | 默认 |
|------|------|------|
| `--level` | 追问深度：quick/standard/deep | quick |
| `--prompt` | 需求描述 | — |
| `--file` | 从文件读取 | — |
| `--output` / `-o` | 输出文件路径 | stdout |
| `--dry-run` | 试运行 | — |

### 3. 博弈融合（fusion）

多个模型独立输出后融合为一份最优报告。

```bash
python3 mll-review.py fusion --prompt "如何提高转化率" --models "gpt-5.5:0.7,claude-opus-4:0.5,gemini-2.5-pro:0.6" -o fusion.md
```

参数：
| 参数 | 说明 |
|------|------|
| `--prompt` | 问题 |
| `--models` | 模型列表，格式：model:temperature,... 必填 |
| `--output` / `-o` | 输出路径 |
| `--dry-run` | 试运行 |

### 4. 单模型对话（chat）

单个模型快速对话。

```bash
python3 mll-review.py chat --model deepseek-v4-flash --prompt "什么意思"
python3 mll-review.py chat --system "你是翻译专家" --prompt "Hello world"
python3 mll-review.py chat --model gpt-5.5 --prompt "详细解释量子计算" --max-tokens 8192
```

参数：
| 参数 | 说明 | 默认 |
|------|------|------|
| `--model` | 指定模型 | gpt-5.5 |
| `--prompt` | 对话内容 | 必填 |
| `--system` | 系统提示词 | — |
| `--max-tokens` | 最大输出 Token | 4096 |
| `--timeout` | 超时秒数 | 自动 |
| `--stream` | 流式输出 | — |

### 5. 更新模型池（pool-update）

```bash
python3 mll-review.py pool-update
```

### 6. 查看模型池（pool-status）

```bash
python3 mll-review.py pool-status
```

## CherryStudio Agent 配置 Key

Agent 设置 → 高级设置 → 环境变量 → 添加 `MLL_KUAI_KEY=***`

## 文件结构

```
mll-review/
├── SKILL.md
└── scripts/
    ├── mll-review.py           ← 评审引擎主入口（819 行）
    ├── model-pool-manager.py   ← 模型池管理
    └── model-pool.json         ← 模型池数据
```

## Key 配置说明

| 方式 | 说明 | 优先级 |
|------|------|--------|
| `--key` 参数 | 每次命令传入 | ⭐ 最高 |
| 环境变量 `MLL_KUAI_KEY` | 系统级或 CherryStudio 环境变量 | ⭐ 中 |

## 注意事项

- 不会自动读取本机 Key 文件或 secrets.json（刻意的安全设计）
- review 和 questioning 的 `--skip-probe` 可跳过模型嗅探加速启动
- fusion 的 `--models` 格式必须为 `model_id:temperature,...`（温度 0-1）
- 每步输出 `📊 扣费汇总`
- 所有原始响应保存到 `.raw.json` 文件
