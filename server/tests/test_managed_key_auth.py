"""JJC-20260818-001：服务端设备级鉴权 managed_key 测试。

覆盖 AC：
  AC2  register 上报 managed_key → devices 表按 device_id 落库绑定可查。
  AC3  已绑定设备用专属 managed_key 经 _authorize timing-safe 放行；错 key 拒绝(401/断开)。
  AC4  未绑定设备首次注册用注册 token 首登申领（device 未落库 managed_key）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import db
from device_registry import DeviceRegistry
from ws_server import WSServer


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    p = tmp_path / "managed_key.db"
    db.init_db(p)
    return p


def _server(tmp_db) -> WSServer:
    return WSServer({"token": "reg-token"}, db_path=tmp_db)


# ---- AC2：register 上报 managed_key → 落库绑定按 device 查询 ----
def test_register_persists_managed_key_query_by_device(tmp_db):
    reg = DeviceRegistry(tmp_db)
    mk = "DEVKEY_bind_001"
    reg.register(device_id="dev-bind", hostname="pc", os_="windows",
                 cherry_version="2.0.3", fork_version="4.0.0",
                 group=None, token="reg-token", managed_key=mk)
    got = reg.get("dev-bind")
    assert got is not None
    assert got["managed_key"] == mk, "devices 表应按 device_id 落库绑定 managed_key"


def test_rebind_empty_does_not_overwrite_existing_key(tmp_db):
    """设备重连/周期上报空 managed_key 时保留已登记 key（不冲掉）。"""
    reg = DeviceRegistry(tmp_db)
    reg.register(device_id="dev-bind2", hostname="pc", os_="linux",
                 cherry_version="2.0.3", fork_version="4.0.0",
                 group=None, token="reg-token", managed_key="DEVKEY_STABLE")
    # 周期上报空 managed_key（reconnect 场景）
    reg.register(device_id="dev-bind2", hostname="pc", os_="linux",
                 cherry_version="2.0.3", fork_version="4.0.0",
                 group=None, token="reg-token", managed_key=None)
    assert reg.get("dev-bind2")["managed_key"] == "DEVKEY_STABLE"


# ---- AC4：未绑定设备首次注册用注册 token 首登申领 ----
def test_unbound_first_login_uses_registration_token(tmp_db):
    srv = _server(tmp_db)
    # 设备从未登记（无 managed_key）→ 走 token 首登申领
    ok = srv._authorize({"type": "register", "device_id": "new-dev-1",
                         "token": "reg-token", "managed_key": ""})
    assert ok is True
    # 错 token → 拒绝
    bad = srv._authorize({"type": "register", "device_id": "new-dev-1",
                          "token": "not-the-token", "managed_key": ""})
    assert bad is False


# ---- AC3：已绑定设备用专属 managed_key 设备级鉴权 ----
def test_bound_device_auth_with_own_key(tmp_db):
    srv = _server(tmp_db)
    reg = DeviceRegistry(tmp_db)
    reg.register(device_id="dev-key-1", hostname="pc", os_="linux",
                 cherry_version="2.0.3", fork_version="4.0.0",
                 group=None, token="reg-token", managed_key="DEVKEY_abc123")
    # 专属 key 正确 → 放行（即便 token 是错的，也应按设备 key 校验）
    ok = srv._authorize({"type": "register", "device_id": "dev-key-1",
                         "token": "wrong-token-ignored", "managed_key": "DEVKEY_abc123"})
    assert ok is True
    # 错 managed_key → 拒绝(401/断开语义)
    bad = srv._authorize({"type": "register", "device_id": "dev-key-1",
                          "token": "reg-token", "managed_key": "ATTACK_key"})
    assert bad is False


def test_bound_device_auth_timing_safe_same_len_wrong(tmp_db):
    """timing-safe：同长度但内容不一的 key 必须拒绝（长度匹配不豁免）。"""
    srv = _server(tmp_db)
    reg = DeviceRegistry(tmp_db)
    bound = "DEVKEY_abc123"  # 12 字符
    reg.register(device_id="dev-ts", hostname="pc", os_="linux",
                 cherry_version="2.0.3", fork_version="4.0.0",
                 group=None, token="reg-token", managed_key=bound)
    wrong_same_len = "DEVKEY_XYZ999"  # 12 字符，内容不同
    assert len(wrong_same_len) == len(bound)
    assert srv._authorize({"type": "register", "device_id": "dev-ts",
                           "managed_key": wrong_same_len}) is False
