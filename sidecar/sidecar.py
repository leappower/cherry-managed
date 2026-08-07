#!/usr/bin/env python3
"""
CherryStudio 企业受管版 · Sidecar 常驻进程 (原型)
====================================================
当前是 M2 原型：验证官方 API 派发能力，不依赖服务端。

功能 (当前版本):
  1. 连接一台 CherryStudio 机器 (从 list.json 或 --host 指定)
  2. 探测并展示官方 API 能力 (agents/models/knowledge)
  3. Agent 派发原型: --deploy-agent 推送一个 Agent 到目标机

用法:
  python3 sidecar.py probe --machine HOST_CHEN   # 探测机器能力
  python3 sidecar.py agents --machine HOST_CHEN   # 列出 Agent
  python3 sidecar.py models --machine HOST_CHEN   # 列出模型
  python3 sidecar.py deploy --machine HOST_CHEN --name "测试Agent" --model deepseek:deepseek-v4-flash --instructions "你是测试助手"

数据源: list.json (机器清单 + Key 映射, 遵循 cs-key.py 铁则, AI 不直接读 Key)
待建:   服务端对接 / 派发回收 / 数据采集 / 完整性校验 (后续里程碑)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 项目根目录 (sidecar/ 的上级)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from cherry_client import CherryClient, CherryError  # noqa: E402


# ── 机器清单加载 (复用 cs-key.py 逻辑) ─────────────────────────
def _find_list_json() -> Path:
    candidates = [
        PROJECT_ROOT / "list.json",
        Path("/Volumes/Chee_2/Chee/OpenClaw_C/cherry-managed/list.json"),
        Path("/Volumes/Chee_2/OpenClaw/CherryStudio/list.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def load_machine(hostname: str) -> dict:
    """从 list.json 加载机器信息 (IP/port/api_key)。"""
    list_path = _find_list_json()
    if not list_path.exists():
        raise SystemExit(f"❌ list.json 未找到 ({list_path})")
    with open(list_path) as f:
        machines = json.load(f)["machines"]
    for m in machines:
        if m["hostname"] == hostname:
            return m
    raise SystemExit(f"❌ 未找到机器: {hostname}")


def get_client(hostname: str) -> CherryClient:
    m = load_machine(hostname)
    return CherryClient.from_machine(m)
def cmd_probe(args):
    c = get_client(args.machine)
    try:
        health = c.health()
        print(f"✅ 健康: {health}")
    except CherryError as e:
        print(f"❌ 连接失败: {e}")
        return 1

    print(f"\n=== Agent 能力 ===")
    try:
        agents = c.list_agents()
        print(f"  Agent 数: {len(agents)}")
        for a in agents[:5]:
            print(f"    - {a.get('name','?')} [{a.get('id','?')[:20]}...] model={a.get('model','?')}")
    except CherryError as e:
        print(f"  ⚠️ agents: {e}")

    print(f"\n=== 模型能力 ===")
    try:
        models = c.list_models()
        print(f"  模型数: {len(models)}")
        for m in models[:8]:
            print(f"    - {m.get('name','?')} (provider: {m.get('provider_name','?')})")
    except CherryError as e:
        print(f"  ⚠️ models: {e}")

    print(f"\n=== 知识库能力 ===")
    try:
        kb = c.list_knowledge_bases()
        print(f"  知识库数: {len(kb)}")
    except CherryError as e:
        print(f"  ⚠️ knowledge: {e}")
    return 0


def cmd_agents(args):
    c = get_client(args.machine)
    agents = c.list_agents()
    if not agents:
        print("(无 Agent)")
        return 0
    for a in agents:
        print(f"  {a.get('name','?'):20s} {a.get('id','?'):32s} model={a.get('model','?')}")
    return 0


def cmd_models(args):
    c = get_client(args.machine)
    models = c.list_models()
    if not models:
        print("(无模型)")
        return 0
    for m in models:
        print(f"  {m.get('name','?'):30s} provider={m.get('provider_name','?'):15s} type={m.get('provider_type','?')}")
    return 0


def cmd_deploy(args):
    """Agent 派发原型: 创建/更新一个 Agent 到目标机。"""
    c = get_client(args.machine)
    if not args.instructions:
        args.instructions = f"你是 {args.name}，一个由公司统一派发的助手。"

    payload = {
        "type": "claude-code",
        "name": args.name,
        "description": args.description or args.name,
        "model": args.model,
        "instructions": args.instructions,
        "accessible_paths": [p.strip() for p in args.accessible_paths.split(",")] if args.accessible_paths else [r"D:\\Caching File\\Cherry Studio\\Data\\Agents\\deployed"],
        "configuration": {
            "permission_mode": "bypassPermissions",
            "max_turns": 100,
            "env_vars": {},
        },
    }

    # 同名已存在 → 更新; 否则创建
    existing = c.find_agent_by_name(args.name)
    if existing:
        aid = existing["id"]
        print(f"🔁 已存在同名 Agent [{args.name}]，更新: {aid}")
        r = c.patch_agent(aid, payload)
        print(f"  ✅ 更新成功: {r}")
    else:
        r = c.create_agent(payload)
        print(f"  ✅ 创建成功: {r}")
    return 0


def main():
    p = argparse.ArgumentParser(description="CherryStudio Sidecar 原型")
    sub = p.add_subparsers(dest="cmd", required=True)

    for sub_cmd, help_text in [
        ("probe", "探测机器 API 能力"),
        ("agents", "列出 Agent"),
        ("models", "列出模型"),
        ("deploy", "派发 Agent"),
    ]:
        sp = sub.add_parser(sub_cmd, help=help_text)
        sp.add_argument("--machine", required=True, help="list.json 中的 hostname")
        if sub_cmd == "deploy":
            sp.add_argument("--name", required=True, help="Agent 名称")
            sp.add_argument("--model", default="deepseek:deepseek-v4-flash", help="模型 ID")
            sp.add_argument("--instructions", default="", help="System Prompt")
            sp.add_argument("--description", default="", help="Agent 描述")
            sp.add_argument("--accessible-paths", default="", help="逗号分隔的路径")

    args = p.parse_args()
    fns = {
        "probe": cmd_probe,
        "agents": cmd_agents,
        "models": cmd_models,
        "deploy": cmd_deploy,
    }
    return fns[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
