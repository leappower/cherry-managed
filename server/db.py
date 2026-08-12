"""SQLite 数据仓库初始化。

按 SDD §4.3 建立服务端 5 张核心表：
  devices / dispatch_log / usage_agg / agent_files / audit_log

SQLite 起步（SDD §5），后续可平滑迁移 PostgreSQL。
所有写操作使用连接级上下文管理器，事务自动提交/回滚。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

# 兼容 config.json 里相对路径（相对 server/ 目录）
SERVER_DIR = Path(__file__).resolve().parent

# 连接池：每线程按 db_path 缓存 sqlite 连接（FastAPI 异步下避免跨线程共用）
_LOCAL = threading.local()


def db_path_from_config(db_path: str) -> Path:
    """把 config 中的 db_path 解析为绝对路径。"""
    p = Path(db_path)
    if not p.is_absolute():
        p = SERVER_DIR / p
    return p


def get_conn(db_path: Path) -> sqlite3.Connection:
    """返回当前线程的 sqlite 连接（懒创建 + 按 path 缓存）。

    注意：按 db_path 区分缓存，避免不同库（如测试临时库 vs 生产库）
    共用同一连接导致数据串写。
    """
    db_path = Path(db_path)
    if not hasattr(_LOCAL, "conns"):
        _LOCAL.conns = {}
    conn = _LOCAL.conns.get(str(db_path))
    if conn is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _LOCAL.conns[str(db_path)] = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id     TEXT PRIMARY KEY,
    hostname      TEXT,
    os            TEXT,
    cherry_version TEXT,
    fork_version  TEXT,
    online        INTEGER DEFAULT 0,
    last_seen     TEXT,
    "group"       TEXT,
    token         TEXT
);

CREATE TABLE IF NOT EXISTS dispatch_log (
    request_id  TEXT PRIMARY KEY,
    device_id   TEXT,
    type        TEXT,
    action      TEXT,
    status      TEXT DEFAULT 'pending',  -- pending / success / fail
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS usage_agg (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT,
    provider      TEXT,
    model         TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens  INTEGER DEFAULT 0,
    period        TEXT
);

CREATE TABLE IF NOT EXISTS agent_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT,
    agent_id    TEXT,
    path        TEXT,
    content     TEXT,
    captured_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    operator   TEXT,
    action     TEXT,
    target     TEXT,
    timestamp  TEXT,
    request_id TEXT
);
"""


def init_db(db_path: Path | str) -> None:
    """初始化数据仓库：建目录 + 建 5 张表。"""
    if isinstance(db_path, str):
        db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    conn.commit()


def table_names(db_path: Path | str) -> list[str]:
    """返回当前库中全部表名（用于验收）。"""
    conn = get_conn(Path(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r["name"] for r in rows]


def audit(db_path: Path, operator: str, action: str, target: str, request_id: str | None = None) -> None:
    """写审计日志（SDD §4.3 audit_log）。"""
    import datetime

    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO audit_log(operator, action, target, timestamp, request_id) VALUES (?,?,?,?,?)",
        (
            operator,
            action,
            target,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            request_id,
        ),
    )
    conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def json_dumps(obj) -> str:
    """设备 agents 列表 JSON 序列化辅助。"""
    return json.dumps(obj, ensure_ascii=False)
