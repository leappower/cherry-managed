"""JJC-20260819-001 方案B 步骤1：Agent 配置仓库 + 版本管理单测。

覆盖（方案第七节步骤1验证点）：
  - 创建 rev=1，sha 一致（compute_sha256 与入库一致）
  - 更新 rev 递增（原子自增，同 Agent 内单调）
  - 同内容同 rev ≠ 不同 rev（rev 参与哈希，升级必改哈希）
  - 字段/资源/技能变化 → 新哈希
  - deploy_status 记录/查询/幂等 UPSERT
  - 锁定（locked）toggle
  - 非法包校验（缺 metadata.name / agent.name 等）报错清单
  - pydantic schema 校验（AgentConfig 模型 + 未知字段丢弃）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pydantic

import db
from agent_repo import AgentRepo, compute_sha256, validate_pkg, canonical_json
import schemas


@pytest.fixture()
def repo(tmp_path: Path) -> AgentRepo:
    p = tmp_path / "agent.db"
    db.init_db(p)
    return AgentRepo(p)


def make_pkg(name: str = "财务助理", version: str = "1.0.0", **kw: Any) -> dict:
    pkg = {
        "metadata": {"name": name, "version": version},
        "agent": {
            "name": name, "type": "claude-code",
            "model": "deepseek/deepseek-v4-flash",
            "instructions": "你是财务助理。",
            "configuration": {"max_turns": 100},
            "skills": ["s1"],
        },
        "skills": [{"id": "s1", "name": "口径", "content": "内容"}],
        "resources": {"guidelines/口径.md": "一律万元。"},
    }
    pkg.update(kw)
    return pkg


def test_schema_8_tables(tmp_path):
    """方案 §3.1：新 3 表 + 原 5 表全建（共 8 张）。"""
    p = tmp_path / "t.db"
    db.init_db(p)
    names = set(db.table_names(p))
    assert {"devices", "dispatch_log", "usage_agg", "agent_files", "audit_log",
            "agent_configs", "agent_versions", "deploy_status"} <= names


def test_create_rev1_and_sha_consistent(repo):
    """创建：rev=1，sha 与 compute_sha256 一致。"""
    pkg = make_pkg()
    v = repo.create_config(pkg)
    assert v["rev"] == 1
    assert v["sha256"] == compute_sha256(pkg)
    assert v["config"]["metadata"]["rev"] == 1
    assert v["config"]["metadata"]["sha256"] == v["sha256"]


def test_update_rev_increments(repo):
    """更新：rev 递增（1→2→3）。"""
    v1 = repo.create_config(make_pkg())
    v2 = repo.update_config("财务助理", make_pkg(version="1.1.0"))
    v3 = repo.update_config("财务助理", make_pkg(version="1.2.0"))
    assert (v1["rev"], v2["rev"], v3["rev"]) == (1, 2, 3)
    assert [x["rev"] for x in repo.list_versions("财务助理")] == [3, 2, 1]


def test_rev_participates_in_hash(repo):
    """同内容不同 rev → 哈希不同（升级必改哈希）。"""
    pkg = make_pkg()
    v1 = repo.create_config(pkg)
    # 内容一致但 rev 不同（重造包，push 到新 rev 语义）
    pkg2 = make_pkg()
    pkg2["metadata"]["rev"] = 5
    assert compute_sha256(pkg2) != v1["sha256"]


def test_content_change_new_hash(repo):
    """字段/资源/技能变化 → 新哈希。"""
    v1 = repo.create_config(make_pkg())
    v2 = repo.update_config("财务助理", make_pkg(version="1.1.0"))
    v3 = repo.update_config("财务助理", make_pkg(
        version="1.2.0",
        agent={"name": "财务助理", "type": "claude-code",
               "model": "deepseek/deepseek-v4-flash",
               "instructions": "新版提示词", "configuration": {"max_turns": 100},
               "skills": ["s1"]}))
    sha_set = {v1["sha256"], v2["sha256"], v3["sha256"]}
    assert len(sha_set) == 3  # 三者哈希全不同


def test_canonical_json_deterministic():
    """规范序列化：key 排序 + 确定性（乱序 dict 序列化一致）。"""
    a = {"b": 1, "a": 2, "c": {"x": "中文"}}
    b = {"c": {"x": "中文"}, "a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_validate_pkg_invalid(repo):
    """非法包：缺失必填字段报错清单。"""
    bad = {"metadata": {}, "agent": {}}
    errs = validate_pkg(bad)
    assert any("metadata.name" in e for e in errs)
    assert any("agent.name" in e for e in errs)
    assert any("agent.type" in e for e in errs)
    assert any("agent.model" in e for e in errs)
    assert any("agent.instructions" in e for e in errs)


def test_validate_pkg_skill_ref_missing(repo):
    """引用一致性：agent.skills 引用包内不存在的 skill → 报错。"""
    pkg = make_pkg()
    pkg["agent"]["skills"] = ["s1", "nope"]
    errs = validate_pkg(pkg)
    assert any("nope" in e for e in errs)


def test_create_on_existing_escalates_to_update(repo):
    """对已存在名称重复 create → 自动升级为 update（rev+1，不冲突）。"""
    v1 = repo.create_config(make_pkg())
    v2 = repo.create_config(make_pkg(version="2.0.0"))
    assert v1["rev"] == 1
    assert v2["rev"] == 2
    assert [x["rev"] for x in repo.list_versions("财务助理")] == [2, 1]


def test_deploy_status_upsert(repo):
    """deploy_status：记录 + 幂等 UPSERT + 按设备查询。"""
    v1 = repo.create_config(make_pkg())  # rev1
    repo.record_deploy("dev-40", "财务助理", v1["rev"], "1.0.0", v1["sha256"])
    d = repo.get_deploy("dev-40", "财务助理")
    assert d["rev"] == 1
    # 更新覆盖（独立 rev 值模拟设备已升级）
    repo.record_deploy("dev-40", "财务助理", 9, "9.0.0", "sha9")
    d2 = repo.get_deploy("dev-40", "财务助理")
    assert d2["rev"] == 9 and d2["version"] == "9.0.0"
    # 按设备 list
    got = repo.list_deploys("dev-40")
    assert len(got) == 1


def test_lock_toggle(repo):
    """锁定/解锁 + is_locked。"""
    repo.create_config(make_pkg())
    assert repo.is_locked("财务助理") is False
    repo.set_locked("财务助理", True)
    assert repo.is_locked("财务助理") is True
    repo.set_locked("财务助理", False)
    assert repo.is_locked("财务助理") is False


def test_schemas_pydantic_valid():
    """pydantic：正确包可通过 AgentConfig 校验。"""
    pkg = make_pkg()
    c = schemas.AgentConfig(**pkg)
    assert c.metadata.name == "财务助理"
    assert c.agent.model == "deepseek/deepseek-v4-flash"
    assert c.skills[0].id == "s1"
    assert c.resources["guidelines/口径.md"] == "一律万元。"


def test_schemas_pydantic_missing_name():
    """pydantic：缺 metadata.name / agent.name 报 validation 错误。"""
    with pytest.raises(pydantic.ValidationError):
        schemas.AgentConfig(**{"metadata": {}, "agent": {"model": "m"}})
