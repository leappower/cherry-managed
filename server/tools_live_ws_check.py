"""端到端验证脚本：连接真实服务端 /ws，注册 + usage 上报 + 接收派发。

用法：python3 live_ws_test.py
需服务端已在 2334 运行。
"""
import asyncio
import json

import websockets

TOKEN = "CHANGE_ME_managed_token"
URL = "ws://127.0.0.1:2334/ws"


async def main():
    async with websockets.connect(URL) as ws:
        # 1. 注册
        await ws.send(json.dumps({
            "type": "register",
            "device_id": "dev-live-001",
            "hostname": "live-pc",
            "os": "windows",
            "cherry_version": "2.0.1",
            "fork_version": "4.0.0-rc.1",
            "group": "sales",
            "token": TOKEN,
        }))
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        print("register_ack:", ack)

        # 2. usage 上报
        await ws.send(json.dumps({
            "type": "usage",
            "device_id": "dev-live-001",
            "period": "2026-08-07T09:00:00Z/2026-08-07T10:00:00Z",
            "records": [
                {"provider": "企_DeepSeek", "model": "deepseek-v4-flash",
                 "input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
            ],
            "errors": [],
        }))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") == "usage_ack":
                print("usage_ack:", msg)
                break

        # 3. status 上报
        await ws.send(json.dumps({
            "type": "status", "device_id": "dev-live-001", "online": True,
            "agents": ["企_客服助手"], "cherry_healthy": True,
        }))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") == "status_ack":
                print("status_ack:", msg)
                break

    print("LIVE_WS_OK")


asyncio.run(main())
