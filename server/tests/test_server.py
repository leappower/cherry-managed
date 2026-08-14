"""服务端骨架单元测试。

覆盖 AC：
  2. WS 连接注册设备 → devices 表
  3. dispatch_agent 派发幂等（同 request_id 不重复创建）
  4. usage 上报 → usage_agg 表
  5. 5 张表全建
  6. 注册 / 派发幂等 / usage 入库 / 重连
  7. 配置化端口 + token 校验（config.json / token 拒绝）
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import db
from device_registry import DeviceRegistry
from dispatch import DispatchService
from collect import CollectService
from ws_server import WSServer, OFFLINE_QUEUE


def run(coro):
    """在独立事件循环中执行协程（pytest 同步环境）。"""
    return asyncio.run(coro)


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    db.init_db(p)
    return p


def test_schema_all_5_tables(tmp_db):
    """AC-5：SQLite 数据仓库 5 张表全建。"""
    names = set(db.table_names(tmp_db))
    assert {"devices", "dispatch_log", "usage_agg", "agent_files", "audit_log"} <= names


def test_device_register_persists(tmp_db):
    """AC-2：注册设备写 devices 表。"""
    reg = DeviceRegistry(tmp_db)
    rec = reg.register(
        device_id="dev-001", hostname="pc1", os_="windows",
        cherry_version="2.0.1", fork_version="4.0.0-rc.1",
        group="sales", token="tok1",
    )
    assert rec["device_id"] == "dev-001"
    assert rec["online"] == 1
    got = reg.get("dev-001")
    assert got["hostname"] == "pc1"
    assert got["group"] == "sales"


def test_device_set_offline(tmp_db):
    """设备离线状态切换。"""
    reg = DeviceRegistry(tmp_db)
    reg.register("dev-001", "pc1", "windows", "2.0.1", None, None, "tok")
    reg.set_offline("dev-001")
    assert reg.get("dev-001")["online"] == 0


class FakeWS:
    def __init__(self, out=None):
        self.out = out if out is not None else []

    async def send(self, data):
        self.out.append(data if isinstance(data, dict) else json.loads(data))

    async def send_json(self, data):
        self.out.append(data)


def test_dispatch_idempotent_same_request_id(tmp_db):
    """AC-3：同 request_id 不重复创建（幂等）。"""
    reg = DeviceRegistry(tmp_db)
    fake = FakeWS()
    reg.attach("dev-001", fake)
    dispatch = DispatchService(tmp_db, reg)

    r1 = run(dispatch.dispatch_agent("dev-001", "create",
                                     {"name": "企_客服助手", "model": "m"},
                                     "http://pkg", "req-001"))
    r2 = run(dispatch.dispatch_agent("dev-001", "create",
                                     {"name": "企_客服助手", "model": "m"},
                                     "http://pkg", "req-001"))
    assert r1["created"] is True
    assert r2["created"] is False  # 幂等：不重复创建
    conn = db.get_conn(tmp_db)
    n = conn.execute(
        "SELECT COUNT(*) c FROM dispatch_log WHERE request_id='req-001'"
    ).fetchone()["c"]
    assert n == 1
    log = dispatch.get_log("req-001")
    assert log["status"] == "pending"


def test_dispatch_offline_enqueue(tmp_db):
    """离线设备指令入队。"""
    reg = DeviceRegistry(tmp_db)
    dispatch = DispatchService(tmp_db, reg)
    OFFLINE_QUEUE.clear()

    r = run(dispatch.dispatch_agent("dev-off", "create",
                                    {"name": "a"}, None, "req-off"))
    assert r["online"] is False
    assert any(did == "dev-off" for did, _ in OFFLINE_QUEUE)


def test_usage_insert(tmp_db):
    """AC-4：usage 上报写 usage_agg 表。"""
    col = CollectService(tmp_db)
    n = col.record_usage(
        "dev-001",
        "2026-08-07T09:00:00Z/2026-08-07T10:00:00Z",
        [{"provider": "企_DeepSeek", "model": "deepseek-v4-flash",
          "input_tokens": 1200, "output_tokens": 800, "total_tokens": 2000}],
    )
    assert n == 1
    rows = col.usage_for("dev-001")
    assert len(rows) == 1
    assert rows[0]["provider"] == "企_DeepSeek"
    assert rows[0]["total_tokens"] == 2000


def test_agent_files_insert(tmp_db):
    """工作目录采集上报写 agent_files 表。"""
    col = CollectService(tmp_db)
    n = col.record_agent_files(
        "dev-001", "agent_客服",
        [{"path": "D:/Agents/deployed/context.txt", "content": "工作上下文"}],
    )
    assert n == 1
    rows = col.agent_files_for("dev-001", "agent_客服")
    assert len(rows) == 1
    assert "context.txt" in rows[0]["path"]


def test_dispatch_result_updates_status(tmp_db):
    """dispatch_result 回执 → status 更新为 success。"""
    reg = DeviceRegistry(tmp_db)
    fake = FakeWS()
    reg.attach("dev-001", fake)
    dispatch = DispatchService(tmp_db, reg)

    run(dispatch.dispatch_agent("dev-001", "create", {"name": "a"}, None, "req-100"))
    dispatch.confirm_result("req-100", True)
    assert dispatch.get_log("req-100")["status"] == "success"


def test_online_dispatch_uses_send_json(tmp_db):
    """在线派发：real starlette WebSocket 需 send_json（send(str) 会误判离线）。

    用只实现 send_json 的 FakeWS（无 .send）模拟 starlette WebSocket，
    验证 dispatch 走 send_json 分支且 online=True。
    """
    class StarletteLikeWS:
        """仅实现 send_json，无 send(str) —— 模拟真实 starlette WebSocket。"""
        def __init__(self):
            self.sent = []
        async def send_json(self, msg):
            self.sent.append(msg)

    reg = DeviceRegistry(tmp_db)
    fake = StarletteLikeWS()
    reg.attach("dev-001", fake)
    dispatch = DispatchService(tmp_db, reg)

    r = run(dispatch.dispatch_agent("dev-001", "create", {"name": "a"}, None, "req-online-json"))
    assert r["online"] is True  # 走 send_json，不被误判为离线
    assert fake.sent and fake.sent[0]["type"] == "dispatch_agent"


def test_fetch_agent_files_online_idempotent(tmp_db):
    """S-6b：fetch_agent_files 在线下发 + 幂等（同 request_id 不重复）。"""
    reg = DeviceRegistry(tmp_db)
    fake = FakeWS()
    reg.attach("dev-001", fake)
    dispatch = DispatchService(tmp_db, reg)

    r1 = run(dispatch.fetch_agent_files("dev-001", "agent_a", ["D:/Agents/deployed"], "req-faf-1"))
    r2 = run(dispatch.fetch_agent_files("dev-001", "agent_a", ["D:/Agents/deployed"], "req-faf-1"))
    assert r1["created"] is True
    assert r1["online"] is True
    assert r2["created"] is False  # 幂等
    # 消息带全部字段
    msgs = [m for m in fake.out if isinstance(m, dict) and m.get("type") == "fetch_agent_files"]
    assert msgs
    assert msgs[0]["device_id"] == "dev-001"
    assert msgs[0]["agent_id"] == "agent_a"
    assert msgs[0]["accessible_paths"] == ["D:/Agents/deployed"]
    assert msgs[0]["request_id"] == "req-faf-1"
    conn = db.get_conn(tmp_db)
    n = conn.execute(
        "SELECT COUNT(*) c FROM dispatch_log WHERE request_id='req-faf-1'"
    ).fetchone()["c"]
    assert n == 1


def test_fetch_agent_files_offline_enqueue(tmp_db):
    """S-6b：fetch_agent_files 离线入队待重连补发。"""
    reg = DeviceRegistry(tmp_db)
    dispatch = DispatchService(tmp_db, reg)
    OFFLINE_QUEUE.clear()

    r = run(dispatch.fetch_agent_files("dev-off", "agent_b", [], "req-faf-off"))
    assert r["online"] is False
    assert any(did == "dev-off" and m.get("type") == "fetch_agent_files"
               for did, m in OFFLINE_QUEUE)


def test_reconnect_flush_offline(tmp_db):
    """重连：重连后补发离线指令（幂等），并从队列移除。"""
    reg = DeviceRegistry(tmp_db)
    dispatch = DispatchService(tmp_db, reg)
    OFFLINE_QUEUE.clear()

    # 离线派发（无连接）
    run(dispatch.dispatch_agent("dev-off", "create", {"name": "a"}, None, "req-recon"))
    assert any(did == "dev-off" for did, _ in OFFLINE_QUEUE)

    # 模拟重连：新 WS 连接，flush 补发
    fake = FakeWS()
    server = WSServer({"token": "tok", "db_path": str(tmp_db)})
    server.registry.attach("dev-off", fake)
    run(server._flush_offline("dev-off", fake))

    # 已补发
    assert any(m.get("request_id") == "req-recon" for m in fake.out)
    # 队列已清空该设备
    assert not any(did == "dev-off" for did, _ in OFFLINE_QUEUE)


def test_config_token_and_port():
    """AC-7：配置化端口 2334 + token 校验。"""
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["port"] == 2334
    assert cfg["token"]

    # token 校验逻辑（WSServer 独立构造）
    server = WSServer({"token": "secret"})
    assert server._authorize({"type": "register", "token": "secret", "device_id": "d"}) is True
    assert server._authorize({"type": "register", "token": "wrong", "device_id": "d"}) is False
