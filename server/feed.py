"""patch_repo feed 管理（批次D：E-2 自建 generic electron-updater feed）。

提供：
- build_latest_yml():  按 electron-builder generic provider 约定生成 latest.yml
- publish_release():   校验安装包存在 → 生成/覆盖 latest.yml
- 供 main.py 挂载静态目录 + 发布 API 调用。
"""
from __future__ import annotations

import base64
import binascii
import datetime
import logging
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# electron-builder generic provider 的 latest.yml 结构字段
_REQUIRED_FILE_FIELDS = ("version", "file_name", "size", "sha512")


def _b64_decode_sha512(sha512_b64: str) -> bytes:
    """校验 sha512 是合法 base64 且长度 64 字节（sha512）。"""
    try:
        raw = base64.b64decode(sha512_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("sha512 必须是合法 base64")
    if len(raw) != 64:
        raise ValueError(f"sha512 base64 解码后须为 64 字节，实际 {len(raw)}")
    return raw


def build_latest_yml(version: str, file_name: str, size: int, sha512_b64: str) -> dict:
    """按 electron-updater generic provider 约定构造 latest.yml dict。"""
    _b64_decode_sha512(sha512_b64)  # 校验合法
    now = datetime.datetime.now(datetime.timezone.utc)
    release_date = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "version": version,
        "files": [{"url": file_name, "sha512": sha512_b64, "size": int(size)}],
        "path": file_name,
        "sha512": sha512_b64,
        "releaseDate": release_date,
    }


def publish_release(patch_repo_dir: Path, version: str, file_name: str,
                    size: int, sha512_b64: str) -> dict:
    """发布一个新版本：校验安装包存在 → 覆盖写 latest.yml。

    返回 {ok, latest_url, version} 或抛 ValueError（校验失败）。
    """
    patch_repo_dir = Path(patch_repo_dir)
    patch_repo_dir.mkdir(parents=True, exist_ok=True)
    pkg = patch_repo_dir / file_name
    if not pkg.is_file():
        raise ValueError(f"安装包不存在: {file_name}")
    # 校验 size 与文件实际大小一致（防元数据错配）
    actual = pkg.stat().st_size
    if int(size) != actual:
        raise ValueError(f"size 不匹配: 声明 {size}, 实际 {actual}")
    # 校验 sha512 合法
    _b64_decode_sha512(sha512_b64)
    latest = build_latest_yml(version, file_name, size, sha512_b64)
    out = patch_repo_dir / "latest.yml"
    out.write_text(yaml.safe_dump(latest, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    logger.info("发布 %s -> %s (size=%s)", version, file_name, size)
    return {
        "ok": True,
        "latest_url": f"/patch_repo/latest.yml",
        "version": version,
        "file": file_name,
        "sha512": sha512_b64,
    }
