"""接收 usage 上报 + 工作目录上报（agent_files）。

SDD §3.5 usage / §2.4 agent-files 采集，§4.3 usage_agg / agent_files 表。

- ``record_usage``：SDD §3.5 批量记录，写 usage_agg 表
- ``record_agent_files``：写 agent_files 表（工作目录上下文+产出，限 accessible_paths）
"""
from __future__ import annotations

import datetime
from pathlib import Path

import db


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class CollectService:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def record_usage(self, device_id: str, period: str, records: list[dict],
                     errors: list | None = None) -> int:
        """SDD §3.5 usage：写入 usage_agg 表。返回写入条数。

        records: [{provider, model, input_tokens, output_tokens, total_tokens}]
        """
        conn = db.get_conn(self.db_path)
        n = 0
        for r in records:
            conn.execute(
                "INSERT INTO usage_agg(device_id, provider, model, input_tokens, "
                "output_tokens, total_tokens, period) VALUES (?,?,?,?,?,?,?)",
                (
                    device_id,
                    r.get("provider"),
                    r.get("model"),
                    r.get("input_tokens", 0),
                    r.get("output_tokens", 0),
                    r.get("total_tokens", 0),
                    period,
                ),
            )
            n += 1
        conn.commit()
        db.audit(self.db_path, "sidecar", "usage_report", device_id)
        return n

    def record_agent_files(self, device_id: str, agent_id: str, files: list[dict]) -> int:
        """工作目录采集上报：写 agent_files 表。

        files: [{path, content}]
        """
        conn = db.get_conn(self.db_path)
        now = _now()
        n = 0
        for f in files:
            conn.execute(
                "INSERT INTO agent_files(device_id, agent_id, path, content, captured_at) "
                "VALUES (?,?,?,?,?)",
                (device_id, agent_id, f.get("path"), f.get("content"), now),
            )
            n += 1
        conn.commit()
        db.audit(self.db_path, "sidecar", "agent_files_report", f"{device_id}:{agent_id}")
        return n

    def usage_for(self, device_id: str | None = None) -> list[dict]:
        conn = db.get_conn(self.db_path)
        if device_id:
            rows = conn.execute(
                "SELECT * FROM usage_agg WHERE device_id=? ORDER BY id DESC LIMIT 500",
                (device_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM usage_agg ORDER BY id DESC LIMIT 500"
            ).fetchall()
        return [dict(r) for r in rows]

    def agent_files_for(self, device_id: str | None = None,
                        agent_id: str | None = None) -> list[dict]:
        conn = db.get_conn(self.db_path)
        sql = "SELECT * FROM agent_files WHERE 1=1"
        params: list = []
        if device_id:
            sql += " AND device_id=?"
            params.append(device_id)
        if agent_id:
            sql += " AND agent_id=?"
            params.append(agent_id)
        sql += " ORDER BY id DESC LIMIT 500"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
