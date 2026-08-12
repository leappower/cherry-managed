"""
测试：collect.py usage 汇总 + 工作目录采集限白名单（防越权）。
"""
from __future__ import annotations

import pytest

from collect import Collector


class FakeFork:
    """ForkClient 替身。"""

    def __init__(self, usage=None, files=None, raise_usage=False, raise_files=False):
        self.usage = usage or []
        self.files = files or []
        self.raise_usage = raise_usage
        self.raise_files = raise_files

    def get_usage(self, from_ts=None, to=None):
        if self.raise_usage:
            raise RuntimeError("usage down")
        return self.usage

    def get_agent_files(self, agent_id=None, path=None):
        if self.raise_files:
            raise RuntimeError("files down")
        return self.files


def test_collect_usage_summarizes(tmp_path):
    fork = FakeFork(usage=[
        {"providerId": "deepseek", "modelId": "v4", "inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
        {"providerId": "deepseek", "modelId": "v4", "inputTokens": 200, "outputTokens": 100, "totalTokens": 300},
        {"provider": "openai", "model": "gpt", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    ])
    c = Collector(fork)
    r = c.collect_usage("http://x/v1/admin/usage")
    assert r["success"] is True
    records = {x["model"]: x for x in r["records"]}
    # deepseek v4 两条合并
    assert records["v4"]["input_tokens"] == 300
    assert records["v4"]["output_tokens"] == 150
    assert records["v4"]["total_tokens"] == 450
    assert records["gpt"]["total_tokens"] == 15


def test_collect_usage_error():
    c = Collector(FakeFork(raise_usage=True))
    r = c.collect_usage("http://x")
    assert r["success"] is False
    assert r["errors"]


def _write(tmp_path, rel, content):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_agent_files_only_within_whitelist(tmp_path):
    """白名单内的采集，越权路径拒绝。"""
    root = tmp_path / "Agents"
    _write(root, "deployed/a1/notes.txt", "in scope")
    _write(root, "secret/leak.txt", "SECRET")   # 白名单外

    fork = FakeFork(files=[
        {"path": str(root / "deployed" / "a1" / "notes.txt")},
        {"path": str(root / "secret" / "leak.txt")},     # 越权
    ])
    c = Collector(fork)
    r = c.collect_agent_files("a1", [str(root / "deployed")])
    assert r["success"] is True
    paths = [f["path"] for f in r["files"]]
    assert str(root / "deployed" / "a1" / "notes.txt") in paths
    assert str(root / "secret" / "leak.txt") not in paths
    # 越权路径被记录进 skipped
    assert any("越权" in s["reason"] for s in r["skipped"])


def test_agent_files_empty_whitelist_denies(tmp_path):
    """accessible_paths 为空 → 拒绝采集。"""
    fork = FakeFork(files=[{"path": "/etc/passwd"}])
    c = Collector(fork)
    r = c.collect_agent_files("a1", [])
    assert r["success"] is False
    assert r["files"] == []


def test_agent_files_error():
    c = Collector(FakeFork(raise_files=True))
    r = c.collect_agent_files("a1", ["/tmp"])
    assert r["success"] is False
    assert r["errors"]
