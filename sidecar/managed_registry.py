"""
受管标记旁路表 — Sidecar 唯一写者 (S-8)
==========================================
⚠️ 老板已拍板 (iii)：修订 SDD 对齐 M1——写 M1 落地格式
   `managed_entity(kind, id, created_at)`（M1 F-8 的 schema），
   位置 {userData}/Data/managed_registry.db。

关键语义（SDD §2.5 泛化受管保护 + 方案 A）：
  - 本表是「受管标记」旁路表，不动官方 schema，无迁移锁库风险（Q7/Q-A3）。
  - **Sidecar 是本表唯一写者**：Fork 层只读，Sidecar 写入。
    派发成功（dispatch_agent/dispatch_provider/dispatch_skills）后在此登记受管项；
    回收（delete/disable/remove）时移除登记。
  - Fork 渲染层 isManaged(id) 读本表 → 受管项锁死 UI / 隐藏删除。
  - 对账（reconcile.py）依据本表判定「受管保护」vs「员工自配非受管」。

M1 F-8 schema（与 Fork 渲染层对齐，created_at 已定稿为 INTEGER epoch 毫秒）：
  CREATE TABLE managed_entity (
      kind       TEXT NOT NULL,   -- 'agent' | 'provider' | 'skill' | 'mcp'
      id         TEXT NOT NULL,   -- 受管项 id
      created_at INTEGER NOT NULL, -- epoch 毫秒（与 Fork Date.now() 一致）
      PRIMARY KEY (kind, id)
  );
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

KINDS = ("agent", "provider", "skill", "mcp")

SCHEMA = """
CREATE TABLE IF NOT EXISTS managed_entity (
    kind       TEXT NOT NULL,
    id         TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (kind, id)
);
"""


def _now() -> int:
    """返回 epoch 毫秒（与 M1 Fork Date.now() 对齐）。"""
    return int(time.time() * 1000)


class ManagedRegistry:
    """受管标记旁路表（managed_entity）。Sidecar 唯一写者。

    线程安全：使用连接级锁，单连接串行写。
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ---- 写（Sidecar 唯一写者）----
    def mark_managed(self, kind: str, item_id: str) -> None:
        """登记受管项（幂等 UPSERT）。kind ∈ agent/provider/skill/mcp。"""
        if kind not in KINDS:
            raise ValueError(f"非法 kind: {kind}（应为 {KINDS}）")
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO managed_entity(kind, id, created_at) VALUES (?,?,?) "
                    "ON CONFLICT(kind, id) DO UPDATE SET created_at=excluded.created_at",
                    (kind, item_id, _now()),
                )
                conn.commit()
            finally:
                conn.close()

    def unmark(self, kind: str, item_id: str) -> None:
        """移除受管标记（回收/删除/禁用时）。"""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "DELETE FROM managed_entity WHERE kind=? AND id=?", (kind, item_id)
                )
                conn.commit()
            finally:
                conn.close()

    def clear(self) -> None:
        """清空全部受管标记（用于初始重建/全量对账）。"""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM managed_entity")
                conn.commit()
            finally:
                conn.close()

    # ---- 读（供 Fork 渲染层 / 对账）----
    def is_managed(self, kind: str, item_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT 1 FROM managed_entity WHERE kind=? AND id=?",
                    (kind, item_id),
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def list_kind(self, kind: str) -> list[str]:
        """返回某 kind 的全部受管 id（如全部受管 agent id）。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id FROM managed_entity WHERE kind=? ORDER BY created_at",
                    (kind,),
                ).fetchall()
                return [r["id"] for r in rows]
            finally:
                conn.close()

    def all(self) -> list[dict]:
        """返回全部受管项 [{kind, id, created_at}]。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT kind, id, created_at FROM managed_entity ORDER BY kind, created_at"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def count(self) -> int:
        with self._lock:
            conn = self._conn()
            try:
                return conn.execute("SELECT COUNT(*) AS c FROM managed_entity").fetchone()["c"]
            finally:
                conn.close()
