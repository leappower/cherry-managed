"""批次G：E-4 安装集成——首次运行(device.json+用户级config) + 配置加载优先级。

覆盖 AC-E4-1（生成 device.json + 用户级 config，非 _MEIPASS）、
AC-E4-4（_load_config 优先序：显式>用户级>内嵌模板，缺失用内嵌落盘）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sidecar  # noqa: E402
from sidecar.sidecar import (  # noqa: E402
    _embedded_config,
    _load_config,
    _user_config_dir,
    USER_CONFIG_DIR_NAME,
    USER_CONFIG_FILE,
)

main = sidecar.sidecar.main  # 真正的入口函数（argv 可注入）


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离用户级配置目录到 tmp，避免污染真实 HOME。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _user_cfg(base: Path) -> Path:
    return Path(base) / USER_CONFIG_DIR_NAME / USER_CONFIG_FILE


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestFirstRun:
    def test_generates_device_and_user_config(self, sandbox):
        code = main(["first-run"])
        assert code == 0, f"first-run exit={code}"
        dev_dir = Path(sandbox) / USER_CONFIG_DIR_NAME
        device_file = dev_dir / "device.json"
        cfg_file = dev_dir / "config.json"
        assert device_file.exists(), "device.json 应落盘（非 _MEIPASS）"
        assert cfg_file.exists(), "用户级 config.json 应落盘（非 _MEIPASS）"
        dev = _load(device_file)
        assert dev["device_id"].startswith("managed-")
        assert dev["hostname"]
        assert dev["os"] in ("windows", "darwin", "linux")
        cfg = _load(cfg_file)
        assert cfg["device"]["device_id"] == dev["device_id"]


class TestFirstRunExplicit:
    def test_explicit_config_materialized_to_user(self, sandbox):
        explicit = Path(sandbox) / "explicit.json"
        explicit.write_text(
            json.dumps({"server": {"url": "ws://explicit.invalid/ws"}}))
        code = main(["first-run", "--config", str(explicit)])
        assert code == 0, f"exit={code}"
        cfg = _load(_user_cfg(sandbox))
        assert cfg["server"]["url"] == "ws://explicit.invalid/ws", \
            "显式 --config 应作为落盘源写入用户级"


class TestLoadConfigPriority:
    def test_explicit_wins_over_user(self, sandbox):
        _user_cfg(sandbox).parent.mkdir(parents=True, exist_ok=True)
        _user_cfg(sandbox).write_text(json.dumps({"server": {"url": "ws://user.invalid/ws"}}))
        explicit = Path(sandbox) / "explicit.json"
        explicit.write_text(json.dumps({"server": {"url": "ws://explicit.invalid/ws"}}))
        cfg = _load_config(explicit=str(explicit))
        assert cfg["server"]["url"] == "ws://explicit.invalid/ws"

    def test_user_level_wins_over_embedded(self, sandbox):
        _user_cfg(sandbox).parent.mkdir(parents=True, exist_ok=True)
        _user_cfg(sandbox).write_text(json.dumps({"server": {"url": "ws://user.invalid/ws"}}))
        cfg = _load_config()  # no explicit → 用户级优先
        assert cfg["server"]["url"] == "ws://user.invalid/ws"

    def test_embedded_template_generated_when_missing(self, sandbox):
        # 无用户级、无显式 → 用内嵌模板生成并落盘
        cfg = _load_config()
        assert _user_cfg(sandbox).exists(), "缺失时应生成用户级配置"
        assert cfg.get("server") is not None
        assert cfg.get("device") is not None

    def test_embedded_path_is_sidecar_config_not_user(self, sandbox):
        # 脚本运行时 _MEIPASS 缺省，内嵌指向 sidecar/config/sidecar.json（非用户级）
        emb = _embedded_config()
        assert emb.name == "sidecar.json"
        assert "CherryManaged" not in str(emb)


class TestServiceSubcommands:
    def test_install_service_linux_returns_zero(self, sandbox):
        # 本机 Linux 无 NSSM：应返回 0 且打印指引（非 Windows 分支）
        code = main(["install-service"])
        assert code == 0

    def test_uninstall_service_linux_returns_zero(self, sandbox):
        code = main(["uninstall-service"])
        assert code == 0
