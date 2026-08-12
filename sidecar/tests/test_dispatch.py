"""
测试：dispatch.py 幂等 + 受管登记 + 四动作。
"""
from __future__ import annotations

import json

import pytest

from dispatch import DispatchExecutor


class FakeCherry:
    """记录调用的 CherryClient 替身。"""

    def __init__(self):
        self.calls = []
        self.agents = {}

    def create_agent(self, payload):
        self.calls.append(("create", payload))
        aid = payload.get("id") or f"agent-{len(self.calls)}"
        self.agents[aid] = payload
        return {"id": aid}

    def patch_agent(self, aid, payload):
        self.calls.append(("patch", aid, payload))
        self.agents.setdefault(aid, {}).update(payload)
        return {"id": aid}

    def put_agent(self, aid, payload):
        self.calls.append(("put", aid, payload))
        self.agents[aid] = payload
        return {"id": aid}

    def delete_agent(self, aid):
        self.calls.append(("delete", aid))
        self.agents.pop(aid, None)
        return {"id": aid}


@pytest.fixture
def registry(tmp_path):
    from managed_registry import ManagedRegistry
    return ManagedRegistry(tmp_path / "reg.db")


@pytest.fixture
def exe(tmp_path, registry):
    cherry = FakeCherry()
    return DispatchExecutor(cherry, registry,
                            deploy_dir=tmp_path / "deploy",
                            log_path=tmp_path / "dispatch.log",
                            skills_dir=tmp_path / "deploy" / "skills")


def test_dispatch_agent_create_marks_managed(exe, registry):
    r = exe.handle_dispatch_agent("create", {"id": "a1", "name": "A"}, request_id="r1")
    assert r["success"] is True
    assert r["agent_id"] == "a1"
    assert registry.is_managed("agent", "a1")


def test_dispatch_agent_delete_unmarks(exe, registry):
    registry.mark_managed("agent", "a1")
    r = exe.handle_dispatch_agent("delete", {"id": "a1"}, request_id="r2")
    assert r["success"] is True
    assert not registry.is_managed("agent", "a1")


def test_dispatch_agent_update_marks(exe, registry):
    r = exe.handle_dispatch_agent("update", {"id": "a1", "name": "A2"}, request_id="r3")
    assert r["success"] is True
    assert registry.is_managed("agent", "a1")


def test_idempotent_same_request_id_no_reexecute(exe, registry):
    first = exe.handle_dispatch_agent("create", {"id": "a9"}, request_id="dup")
    # 强制：同 request_id 第二次调用（模拟重发）应幂等跳过，不再调用 cherry
    n_calls_before = len(exe.cherry.calls)
    second = exe.handle_dispatch_agent("create", {"id": "a9"}, request_id="dup")
    assert second["idempotent"] is True
    assert len(exe.cherry.calls) == n_calls_before  # 未重复执行


def test_idempotent_persisted_across_restart(tmp_path, registry):
    """dispatch_log 持久化：重启后同 request_id 仍幂等。"""
    log_path = tmp_path / "dispatch.log"
    exe1 = DispatchExecutor(FakeCherry(), registry,
                            deploy_dir=tmp_path / "deploy1",
                            log_path=log_path)
    exe1.handle_dispatch_agent("create", {"id": "aX"}, request_id="persist1")
    # 新实例（模拟重启）加载同一 log
    exe2 = DispatchExecutor(FakeCherry(), registry,
                            deploy_dir=tmp_path / "deploy2",
                            log_path=log_path)
    r = exe2.handle_dispatch_agent("create", {"id": "aX"}, request_id="persist1")
    assert r["idempotent"] is True


def test_dispatch_provider_add_remove(exe, registry):
    r = exe.handle_dispatch_provider("add", {"id": "p1", "name": "P"}, request_id="rp1")
    assert r["success"] is True
    assert registry.is_managed("provider", "p1")
    r2 = exe.handle_dispatch_provider("remove", {"id": "p1"}, request_id="rp2")
    assert r2["success"] is True
    assert not registry.is_managed("provider", "p1")


def test_dispatch_skills_marks(exe, registry):
    r = exe.handle_dispatch_skills(
        [{"id": "s1", "name": "S1", "version": "1.0"}], request_id="rs1")
    assert r["success"] is True
    assert r["installed"] == [{"id": "s1", "name": "S1", "version": "1.0"}]
    assert registry.is_managed("skill", "s1")
    # 落盘
    assert (exe.skills_dir / "s1.json").exists()


def test_unknown_action_fails(exe):
    r = exe.handle_dispatch_agent("explode", {"id": "a1"}, request_id="rx")
    assert r["success"] is False
    assert "未知 action" in r["error"]
