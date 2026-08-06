#!/usr/bin/env python3
"""
CherryStudio API 密钥管理器 — 统一 Key 映射入口
================================================
用途：所有 CherryStudio Agent 远程操作统一通过此脚本获取 Key，
      不允许大模型直接读 list.json 解析 Key。
      
用法：
  python3 cs-key.py get <hostname>        # 获取指定机器的 Key
  python3 cs-key.py list                  # 列出所有机器
  python3 cs-key.py curl <hostname> <args>  # 用指定机器的 Key 执行 curl
  python3 cs-key.py api <hostname> <path>  # 快速 GET 请求

示例：
  python3 cs-key.py list
  python3 cs-key.py get HOST_CHEN
  python3 cs-key.py curl HOST_LIANG -s http://HOST_69:23333/v1/agents
  python3 cs-key.py api HOST_LIANG /v1/agents
"""

import json, subprocess, sys, os

def _find_list():
    paths = [
        "/Volumes/Chee_2/Chee/OpenClaw_C/CherryStudioAgent远程控制/list.json",
        "/Volumes/Chee_2/OpenClaw/CherryStudio/list.json",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[0]

LIST_PATH = _find_list()

def load_machines():
    if not os.path.exists(LIST_PATH):
        print(f"❌ list.json 未找到 ({LIST_PATH})", file=sys.stderr)
        sys.exit(1)
    with open(LIST_PATH) as f:
        return json.load(f)["machines"]

def find_machine(hostname):
    for m in load_machines():
        if m["hostname"] == hostname:
            return m
    print(f"❌ 未找到机器: {hostname}", file=sys.stderr)
    sys.exit(1)

def cmd_list():
    machines = load_machines()
    print(f"{'hostname':20s} {'alias':16s} {'IP':18s} {'status':10s} {'Key':40s}")
    print("-" * 110)
    for m in machines:
        key = m.get("api_key", "")
        k_display = key[:20] + "..." + key[-6:] if len(key) > 28 else key
        print(f"{m['hostname']:20s} {m.get('alias',''):16s} {m['ip']:18s} {m.get('status',''):10s} {k_display:40s}")

def cmd_get(hostname):
    m = find_machine(hostname)
    print(m["api_key"], end="")

def cmd_curl(hostname, curl_args):
    m = find_machine(hostname)
    key = m["api_key"]
    host = f"http://{m['ip']}:{m['port']}"
    
    cmd = ["curl"]
    # 如果有 -H Authorization，替换为正确的 Key
    has_auth = False
    for i, arg in enumerate(curl_args):
        if arg.startswith("Authorization:"):
            curl_args[i] = f"Authorization: Bearer {key}"
            has_auth = True
        elif arg.lower().startswith("bearer "):
            curl_args[i] = f"Bearer {key}"
            has_auth = True
    
    if not has_auth:
        # 如果没有 Auth header，自动加上
        cmd += ["-H", f"Authorization: Bearer {key}"]
    
    cmd += curl_args
    
    # 替换 URL 中的 %HOST%
    cmd = [host + arg if arg.startswith("/") else arg for arg in cmd]
    # 或者直接替换
    cmd = [arg.replace("%HOST%", host) for arg in cmd]
    
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    return r.returncode

def cmd_api(hostname, path, method="GET", data=None):
    m = find_machine(hostname)
    key = m["api_key"]
    host = f"http://{m['ip']}:{m['port']}"
    url = f"{host}{path}"
    
    cmd = ["curl", "-s", "-H", f"Authorization: Bearer {key}"]
    if method == "PATCH":
        cmd += ["-X", "PATCH", "-H", "Content-Type: application/json"]
        if data:
            cmd += ["-d", data]
    elif method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json"]
        if data:
            cmd += ["-d", data]
    cmd += [url]
    
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, end="")
    return r

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == "list":
        cmd_list()
    elif action == "get" and len(sys.argv) >= 3:
        cmd_get(sys.argv[2])
    elif action == "curl" and len(sys.argv) >= 4:
        sys.exit(cmd_curl(sys.argv[2], sys.argv[3:]))
    elif action == "api" and len(sys.argv) >= 4:
        cmd_api(sys.argv[2], sys.argv[3])
    elif action == "patch" and len(sys.argv) >= 5:
        cmd_api(sys.argv[2], sys.argv[3], "PATCH", sys.argv[4])
    elif action == "post" and len(sys.argv) >= 5:
        cmd_api(sys.argv[2], sys.argv[3], "POST", sys.argv[4])
    else:
        print(f"❌ 未知命令: {action}", file=sys.stderr)
        sys.exit(1)
