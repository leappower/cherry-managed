"""
测试：reconcile.py 受管 vs 非受管区分。
"""
from __future__ import annotations

import pytest

from reconcile import ReconcileEngine, reconcile


class FakeRegistry:
    """ManagedRegistry 替身（只读 all()/is_managed()）。"""

    def __init__(self, rows):
        # rows: [{kind, id, created_at}]
        self.rows = rows
        self._ids = {(r["kind"], r["id"]) for r in rows}

    def all(self):
        return [dict(r) for r in self.rows]

    def is_managed(self, kind, item_id):
        return (kind, item_id) in self._ids


def _reg(*kinds_ids):
    rows = [{"kind": k, "id": i, "created_at": "2026-01-01"}
            for k, i in kinds_ids]
    return FakeRegistry(rows)


def test_missing_expected_detected():
    """期望有但本地无受管 → missing（需补）。"""
    reg = _reg(("agent", "a1"))
    engine = ReconcileEngine(reg)
    expected = [
        {"kind": "agent", "id": "a1", "name": "A1"},   # 已受管
        {"kind": "agent", "id": "a2", "name": "A2"},   # 缺
    ]
    r = engine.reconcile(expected)
    missing_ids = {m["id"] for m in r["missing"]}
    assert "a2" in missing_ids
    assert "a1" not in missing_ids  # a1 已有 → 不补


def test_unmanaged_extra_ignored():
    """本地受管之外的（员工自配非受管）不进入 missing/modified。"""
    reg = _reg(("agent", "a1"))
    engine = ReconcileEngine(reg)
    # 期望只有 a1；本地受管也只有 a1
    expected = [{"kind": "agent", "id": "a1"}]
    r = engine.reconcile(expected)
    # 没有 extra_unmanaged：因为非受管项根本不在旁路表里，无法枚举，天然忽略
    assert r["extra_unmanaged"] == []
    assert r["summary"]["extra_unmanaged"] == 0
    # 也不该出现误删受管项
    assert r["managed_extra"] == []


def test_managed_extra_detected():
    """本地受管但期望中不存在 → managed_extra（需回收）。"""
    reg = _reg(("agent", "a1"), ("agent", "a2"))
    engine = ReconcileEngine(reg)
    expected = [{"kind": "agent", "id": "a1"}]
    r = engine.reconcile(expected)
    extra = {e["id"] for e in r["managed_extra"]}
    assert extra == {"a2"}


def test_modified_when_version_differs():
    """受管项内容签名不一致 → modified（需修）。"""
    reg = _reg(("agent", "a1"))
    engine = ReconcileEngine(reg)
    expected = [{"kind": "agent", "id": "a1", "version": "2.0"}]
    r = engine.reconcile(expected)
    # 本地旁路表无 version 信息 → 签名不匹配 → modified
    assert len(r["modified"]) == 1
    assert r["modified"][0]["id"] == "a1"


def test_reconcile_module_function():
    """模块级便捷函数可调用。"""
    reg = _reg(("agent", "a1"))
    r = reconcile([{"kind": "agent", "id": "a2"}], reg)
    assert r["summary"]["missing"] == 1
