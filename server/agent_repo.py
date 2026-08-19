"""JJC-20260819-001 方案B：Agent 配置仓库 + 版本管理（服务端）。

职责：
  - agent_configs 最新态（name 主键 → latest_rev/latest_sha/locked）
  - agent_versions 版本历史（rev 自增、配置快照、sha256、build_info）
  - deploy_status 设备级部署状态（对账锚点）
  - 内容指纹 sha256（字段变更即新哈希；rev 参与哈希保证「升级必改哈希」）
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import db


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def canonical_json(obj: Any) -> str:
    """规范序列化：key 排序 + ensure_ascii=False，用于哈希与对账（确定性）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_sha256(pkg: dict) -> str:
    """内容指纹（spec 2.6 recipe）。

    ``name`` -> ``rev`` -> canonical(agent) -> resources(路径字典序) -> skills(id 字典序)
    任何字段/资源/技能变化都会产生新哈希；rev 参与哈希保证「升级必改哈希」。
    """
    md = hashlib.sha256()
    md.update(b"name=" + str(pkg.get("metadata", {}).get("name", "")).encode("utf-8"))
    md.update(b"|rev=" + str(pkg.get("metadata", {}).get("rev", "")).encode("utf-8"))
    md.update(b"|" + canonical_json(pkg.get("agent", {})).encode("utf-8"))
    resources = pkg.get("resources") or {}
    for path in sorted(resources.keys()):
        md.update(b"|resources:" + str(path).encode("utf-8") + b"=" + str(resources[path]).encode("utf-8"))
    skills = pkg.get("skills") or []
    for sk in sorted(skills, key=lambda s: str(s.get("id", ""))):
        md.update(b"|skills:" + str(sk.get("id", "")).encode("utf-8") + b"=" + str(sk.get("content", "")).encode("utf-8"))
    return md.hexdigest()


class AgentRepo:
    """Agent 配置仓库（agent_configs + agent_versions + deploy_status 统一入口）。"""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    # ---- 创建/更新（rev 递增 + 版本历史） ----
    def create_config(self, pkg: dict, created_by: str | None = None) -> dict:
        """创建 Agent 配置：rev=1，写 agent_configs + agent_versions。

        返回写入后的版本记录 dict（含 rev/sha256）。
        """
        name = pkg["metadata"]["name"]
        version = pkg["metadata"].get("version", "1.0.0")
        # 已存在同名配置：自动升级为 update（rev+1），避免 UNIQUE(name,rev) 冲突
        conn0 = db.get_conn(self.db_path)
        exists = conn0.execute("SELECT 1 FROM agent_configs WHERE name=?", (name,)).fetchone()
        if exists:
            return self.update_config(name, pkg, created_by)
        pkg.setdefault("metadata", {})["name"] = name
        pkg["metadata"]["rev"] = 1
        pkg["metadata"]["created_at"] = _now()
        sha = compute_sha256(pkg)
        pkg["metadata"]["sha256"] = sha
        now = _now()
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO agent_versions(name, rev, version, config, sha256, created_by, created_at, build_info) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, 1, version, json.dumps(pkg, ensure_ascii=False), sha, created_by, now,
             json.dumps(pkg.get("metadata", {}).get("build_info"))),
        )
        conn.execute(
            "INSERT INTO agent_configs(name, latest_rev, latest_sha, updated_at, locked) "
            "VALUES (?,?,?,?,0) "
            "ON CONFLICT(name) DO UPDATE SET latest_rev=excluded.latest_rev, "
            "latest_sha=excluded.latest_sha, updated_at=excluded.updated_at",
            (name, 1, sha, now),
        )
        conn.commit()
        db.audit(self.db_path, created_by or "admin", "agent_config_create", f"{name}:rev1", None)
        rec = self.get_version(name, 1)
        if rec is None:  # 刚写入必存在（防御性，满足类型标注）
            raise RuntimeError(f"create_config 后取回版本失败: {name}:1")
        return rec

    def update_config(self, name: str, pkg: dict, created_by: str | None = None) -> dict:
        """更新 Agent 配置：rev+1（原子递增），写新版本历史，更新最新态。

        rev 原子递增：``(SELECT COALESCE(MAX(rev),0)+1 ...)`` 保证同 Agent 单调。
        """
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT COALESCE(MAX(rev),0)+1 AS next_rev FROM agent_versions WHERE name=?",
            (name,),
        ).fetchone()
        next_rev = row["next_rev"]
        version = pkg.get("metadata", {}).get("version", f"1.{next_rev}.0")
        pkg.setdefault("metadata", {})["name"] = name
        pkg["metadata"]["rev"] = next_rev
        pkg["metadata"]["created_at"] = _now()
        sha = compute_sha256(pkg)
        pkg["metadata"]["sha256"] = sha
        now = _now()
        conn.execute(
            "INSERT INTO agent_versions(name, rev, version, config, sha256, created_by, created_at, build_info) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, next_rev, version, json.dumps(pkg, ensure_ascii=False), sha, created_by, now,
             json.dumps(pkg.get("metadata", {}).get("build_info"))),
        )
        conn.execute(
            "UPDATE agent_configs SET latest_rev=?, latest_sha=?, updated_at=? WHERE name=?",
            (next_rev, sha, now, name),
        )
        conn.commit()
        db.audit(self.db_path, created_by or "admin", "agent_config_update", f"{name}:rev{next_rev}", None)
        rec = self.get_version(name, next_rev)
        if rec is None:  # 刚写入必存在（防御性，满足类型标注）
            raise RuntimeError(f"update_config 后取回版本失败: {name}:{next_rev}")
        return rec

    # ---- 查询 ----
    def get_version(self, name: str, rev: int) -> dict | None:
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT id, name, rev, version, config, sha256, created_by, created_at, build_info "
            "FROM agent_versions WHERE name=? AND rev=?",
            (name, rev),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["config"] = json.loads(d["config"])
        d["build_info"] = json.loads(d["build_info"]) if d.get("build_info") else None
        return d

    def get_latest(self, name: str) -> dict | None:
        """取最新 rev 的完整版本记录（含 config 解析）。"""
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT COALESCE(latest_rev,0) AS r FROM agent_configs WHERE name=?", (name,)
        ).fetchone()
        if row is None or row["r"] == 0:
            return None
        return self.get_version(name, row["r"])

    def list_versions(self, name: str) -> list[dict]:
        """版本历史（rev 倒序），不含 config 正文（轻量列表）。"""
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT name, rev, version, sha256, created_by, created_at FROM agent_versions "
            "WHERE name=? ORDER BY rev DESC",
            (name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_configs(self) -> list[dict]:
        """配置包列表（agent_configs 最新态）。"""
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT name, latest_rev, latest_sha, updated_at, locked FROM agent_configs ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            latest = self.get_version(d["name"], d["latest_rev"])
            d["version"] = latest["version"] if latest else None
            out.append(d)
        return out

    def get_config(self, name: str) -> dict | None:
        """包详情（agent_configs 最新态 + 最新版本内容）。"""
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT name, latest_rev, latest_sha, updated_at, locked FROM agent_configs WHERE name=?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        latest = self.get_version(name, d["latest_rev"])
        d["config"] = latest["config"] if latest else None
        return d

    def set_locked(self, name: str, locked: bool) -> None:
        conn = db.get_conn(self.db_path)
        conn.execute("UPDATE agent_configs SET locked=? WHERE name=?", (1 if locked else 0, name))
        conn.commit()

    def is_locked(self, name: str) -> bool:
        conn = db.get_conn(self.db_path)
        row = conn.execute("SELECT locked FROM agent_configs WHERE name=?", (name,)).fetchone()
        return bool(row and row["locked"])

    # ---- 设备部署状态（对账锚点） ----
    def record_deploy(self, device_id: str, agent_name: str, rev: int,
                      version: str | None, sha256: str | None) -> None:
        """记录/更新设备已部署 rev（UPSERT，幂等）。"""
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO deploy_status(device_id, agent_name, rev, version, sha256, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(device_id, agent_name) DO UPDATE SET rev=excluded.rev, "
            "version=excluded.version, sha256=excluded.sha256, updated_at=excluded.updated_at",
            (device_id, agent_name, rev, version, sha256, _now()),
        )
        conn.commit()

    def get_deploy(self, device_id: str, agent_name: str) -> dict | None:
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT device_id, agent_name, rev, version, sha256, updated_at "
            "FROM deploy_status WHERE device_id=? AND agent_name=?",
            (device_id, agent_name),
        ).fetchone()
        return dict(row) if row else None

    def list_deploys(self, device_id: str | None = None) -> list[dict]:
        conn = db.get_conn(self.db_path)
        if device_id:
            rows = conn.execute(
                "SELECT device_id, agent_name, rev, version, sha256, updated_at "
                "FROM deploy_status WHERE device_id=? ORDER BY agent_name",
                (device_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT device_id, agent_name, rev, version, sha256, updated_at "
                "FROM deploy_status ORDER BY device_id, agent_name"
            ).fetchall()
        return [dict(r) for r in rows]


def validate_pkg(pkg: dict) -> list[str]:
    """包结构校验（spec 2.x）。返回错误列表；空列表=合法。"""
    errors: list[str] = []
    metadata = pkg.get("metadata")
    if not isinstance(metadata, dict):
        return ["metadata 缺失或非对象"]
    if not metadata.get("name"):
        errors.append("metadata.name 必填")
    agent = pkg.get("agent")
    if not isinstance(agent, dict):
        errors.append("agent 缺失或非对象")
    else:
        if not agent.get("name"):
            errors.append("agent.name 必填")
        if not agent.get("type"):
            errors.append("agent.type 必填")
        if not agent.get("model"):
            errors.append("agent.model 必填")
        if not agent.get("instructions"):
            errors.append("agent.instructions 必填")
        if "configuration" in agent and not isinstance(agent["configuration"], dict):
            errors.append("agent.configuration 必须是对象")
    # 引用一致性：agent.skills[] 引用须在包内 skills[] 存在（若有）
    agent_skills = (agent or {}).get("skills") or []
    pkg_skills = {s.get("id") for s in (pkg.get("skills") or [])}
    for sid in agent_skills:
        if sid and sid not in pkg_skills:
            errors.append(f"agent.skills 引用 '{sid}' 在包内 skills 中不存在")
    return errors


def version_semver_ok(version: str) -> bool:
    """语义化版本合法性：x.y.z（可选 -rc.N 后缀）。"""
    return bool(re.match(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$", version))
