"""批次D：E-2 自建更新通道测试。

覆盖：
- feed.build_latest_yml 结构正确（version/files/path/sha512/releaseDate）
- feed.publish_release 发布成功 + latest.yml 落盘 + sha512 校验
- main.py 发布 API（鉴权 + 生成 + 下载）
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

import feed  # noqa: E402


def _b64_sha512(data: bytes) -> str:
    return base64.b64encode(hashlib.sha512(data).digest()).decode()


class TestBuildLatestYml:
    def test_structure(self):
        y = feed.build_latest_yml("1.2.3", "CherryStudio-Setup-1.2.3.exe", 100, _b64_sha512(b"x"))
        assert y["version"] == "1.2.3"
        assert y["files"][0] == {"url": "CherryStudio-Setup-1.2.3.exe",
                                 "sha512": _b64_sha512(b"x"), "size": 100}
        assert y["path"] == "CherryStudio-Setup-1.2.3.exe"
        assert y["sha512"] == _b64_sha512(b"x")
        assert y["releaseDate"].endswith("Z")

    def test_bad_sha512(self):
        with pytest.raises(ValueError):
            feed.build_latest_yml("1.0", "a.exe", 1, "not-base64!!!")


class TestPublishRelease:
    def test_publish_writes_latest_yml(self, tmp_path):
        pkg = tmp_path / "CherryStudio-Setup-1.0.0.exe"
        pkg.write_bytes(b"\x00\x01\x02")
        r = feed.publish_release(tmp_path, "1.0.0", pkg.name, pkg.stat().st_size,
                                 _b64_sha512(pkg.read_bytes()))
        assert r["ok"] is True
        assert (tmp_path / "latest.yml").is_file()
        import yaml
        data = yaml.safe_load((tmp_path / "latest.yml").read_text())
        assert data["version"] == "1.0.0"
        assert data["path"] == pkg.name

    def test_missing_package(self, tmp_path):
        with pytest.raises(ValueError):
            feed.publish_release(tmp_path, "1.0.0", "nope.exe", 5, _b64_sha512(b"n"))

    def test_size_mismatch(self, tmp_path):
        pkg = tmp_path / "a.exe"
        pkg.write_bytes(b"abc")
        with pytest.raises(ValueError):
            feed.publish_release(tmp_path, "1.0", pkg.name, 999, _b64_sha512(b"abc"))

    def test_creates_dir(self, tmp_path):
        sub = tmp_path / "sub" / "repo"
        pkg = sub / "b.exe"
        sub.mkdir(parents=True)
        pkg.write_bytes(b"d")
        r = feed.publish_release(sub, "0.9", pkg.name, 1, _b64_sha512(b"d"))
        assert r["ok"] is True


# ---- main.py API 集成测试 ----
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app)
TOKEN = main.CONFIG["token"]


class TestReleaseAPI:
    def _make_pkg(self, name="CherryStudio-Setup-9.9.9.exe"):
        pkg = main.PATCH_REPO_DIR / name
        pkg.write_bytes(os.urandom(64))
        return pkg

    def test_publish_requires_token(self):
        pkg = self._make_pkg()
        r = client.post("/api/release/publish",
                        json={"version": "9.9.9", "file_name": pkg.name,
                              "size": pkg.stat().st_size, "sha512": _b64_sha512(pkg.read_bytes())})
        assert r.status_code == 401

    def test_publish_ok(self):
        pkg = self._make_pkg()
        r = client.post("/api/release/publish",
                        headers={"X-Token": TOKEN},
                        json={"version": "9.9.9", "file_name": pkg.name,
                              "size": pkg.stat().st_size, "sha512": _b64_sha512(pkg.read_bytes())})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["version"] == "9.9.9"

    def test_latest_yml_served(self):
        r = client.get("/patch_repo/latest.yml")
        assert r.status_code == 200
        import yaml
        data = yaml.safe_load(r.text)
        assert data["version"] == "9.9.9"

    def test_package_served_and_sha512(self):
        pkg = main.PATCH_REPO_DIR / "CherryStudio-Setup-9.9.9.exe"
        r = client.get("/patch_repo/CherryStudio-Setup-9.9.9.exe")
        assert r.status_code == 200
        assert r.content == pkg.read_bytes()
        assert _b64_sha512(r.content) == _b64_sha512(pkg.read_bytes())
