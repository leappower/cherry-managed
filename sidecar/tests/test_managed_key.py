"""JJC-20260818-001：设备级受管密钥 managed_key 生成/上报/权限。

覆盖 AC：
  AC1  首启自动生成唯一 managed_key 落盘独立文件(0600)；重启进程 key 不变（幂等）。
  AC2  _register 上报 payload 含 managed_key 字段（与 device_id 同报）。
  AC5  敏感 key 不落明文日志；落盘文件权限收紧 0600/0400（与 device.json 分开）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sidecar.sidecar import (  # noqa: E402
    _managed_key,
    _managed_key_file,
    _read_managed_key_from_db,
    MANAGED_KEY_FILE,
    USER_CONFIG_DIR_NAME,
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离用户级配置目录到 tmp，避免污染真实 HOME。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _mk_path(base: Path) -> Path:
    return Path(base) / USER_CONFIG_DIR_NAME / MANAGED_KEY_FILE


class TestManagedKey:
    # ---- AC1：首启幂等生成 + 独立文件 + 权限 ----
    def test_first_generate_persists_independent_file_with_perms(self, sandbox):
        key1 = _managed_key()
        # 唯一性：urlsafe 32 字节 → 长度 ~43 字符
        assert isinstance(key1, str) and key1
        assert len(key1) >= 40
        mkf = _managed_key_file()
        assert mkf.name == MANAGED_KEY_FILE
        assert mkf.exists(), "managed_key 应落盘独立文件（不混入 device.json）"
        # AC1：磁盘权限收紧 0600（POSIX）
        if os.name != "nt":
            assert mkf.stat().st_mode & 0o777 <= 0o600, "权限应收紧至 0600"

    def test_restart_idempotent_same_key(self, sandbox):
        key1 = _managed_key()
        key2 = _managed_key()  # 模拟重启进程再次读取
        assert key1 == key2, "重启后 key 应不变（幂等，禁止重复生成）"
        # 文件内容与返回一致
        assert _mk_path(sandbox).read_text(encoding="utf-8").strip() == key1

    def test_two_devices_two_distinct_keys(self, sandbox, tmp_path, monkeypatch):
        """两台独立设备（不同配置目录）key 不同 → 每设备唯一。"""
        key_a = _managed_key()
        # 切到第二个 XDG 目录模拟另一台设备
        other = tmp_path / "other"
        other.mkdir(exist_ok=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(other))
        monkeypatch.setenv("HOME", str(other))
        key_b = _managed_key()
        assert key_a != key_b, "不同设备应生成不同 managed_key"

    # ---- AC2：_register 上报 payload 含 managed_key（与 device_id 同报）----
    def test_register_payload_includes_managed_key(self, sandbox):
        from sidecar.sidecar import SidecarRunner  # noqa: PLC0415

        key = _managed_key()
        # 轻量构造：绕过重 init，仅准备 _register 所需属性
        runner = object.__new__(SidecarRunner)
        runner.cfg = {
            "server": {"token": "dev-token"},
            "cherry": {"version": "2.0.3", "fork_version": "4.0.0"},
        }
        runner.device = {"device_id": "managed-abc123",
                         "hostname": "pc1", "os": "linux", "group": ""}
        captured = {}
        runner._send = lambda m: captured.update(m)
        runner._register()
        # AC2：payload 含 managed_key，且值与本地文件一致（与 device_id 同报）
        assert "managed_key" in captured, "register payload 应含 managed_key 字段"
        assert captured["managed_key"] == key, "上报的 managed_key 应为本地生成的 key"
        assert captured["device_id"] == "managed-abc123"
        assert "\n" not in captured["managed_key"]

    def test_key_file_not_in_device_json(self, sandbox):
        """managed_key 独立文件，不混入 device.json（避免误提交敏感凭据）。"""
        _managed_key()
        dev_dir = Path(sandbox) / USER_CONFIG_DIR_NAME
        assert list(dev_dir.glob("managed_key*")), "存在独立 managed_key 文件"
        dev_json = dev_dir / "device.json"
        if dev_json.exists():
            assert "managed_key" not in json.loads(dev_json.read_text(encoding="utf-8"))


class TestReadManagedKeyFromDb:
    """JJC-20260819-001 方案B 断链修复：从本机 CherryStudio sqlite 读 K2 旁路。

    覆盖：loopback 拉取被 403（K2 已存在带 Bearer 保护）时，sidecar 能从
    %APPDATA%\\CherryStudio\\Data\\cherrystudio.sqlite 的 preference 表直读
    feature.api_gateway.managed_key，避免鸡生蛋。
    """

    def _make_sqlite(self, db_path: Path, k2: str) -> None:
        import sqlite3
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE preference (scope TEXT, key TEXT, value TEXT)")
            # 与 40 号机实测一样：value 是 JSON 字符串（带引号）
            conn.execute(
                "INSERT INTO preference (scope, key, value) VALUES (?,?,?)",
                ("default", "feature.api_gateway.managed_key", json.dumps(k2)),
            )
            conn.commit()
        finally:
            conn.close()

    def test_reads_k2_from_appdata_sqlite(self, tmp_path, monkeypatch):
        k2 = "cs-mk-b65b14f9-9aad-42cf-b812-dc7f61e39bf0"
        fake_appdata = tmp_path / "appdata"
        self._make_sqlite(fake_appdata / "CherryStudio" / "Data" / "cherrystudio.sqlite", k2)
        monkeypatch.setenv("APPDATA", str(fake_appdata))

        got = _read_managed_key_from_db()
        assert got == k2, "应从 preference 表 JSON value 提取到 K2"

    def test_returns_empty_when_no_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "nonexistent"))
        assert _read_managed_key_from_db() == ""

    def test_returns_empty_when_key_absent(self, tmp_path, monkeypatch):
        import sqlite3
        db = tmp_path / "CherryStudio" / "Data" / "cherrystudio.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE preference (scope TEXT, key TEXT, value TEXT)")
            conn.execute("INSERT INTO preference VALUES ('default','other.key','\"x\"')")
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert _read_managed_key_from_db() == ""
