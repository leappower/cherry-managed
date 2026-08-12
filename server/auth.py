"""管理员登录鉴权（D-2 Web 管理后台）。

单管理员 + 密码哈希（config.json 的 admin_user / admin_password_hash）+ 随机 session token。
轻量实现，不引入重鉴权框架、不引入外部依赖（仅标准库）。

- ``hash_password`` / ``verify_password``：PBKDF2-HMAC-SHA256 密码哈希（带随机盐）
- ``AdminAuth``：登录发 token + 内存 session 校验

FastAPI 鉴权依赖 ``require_admin`` 在 main.py 中定义（绑定全局 auth 实例）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

SCHEME = "pbkdf2"
_ITERATIONS = 100_000


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    """生成 PBKDF2-HMAC-SHA256 密码哈希字符串。

    格式：``pbkdf2$<iterations>$<salt_hex>$<hash_hex>``
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{SCHEME}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与存储哈希是否匹配（恒定时间比较）。"""
    if not stored or not isinstance(stored, str):
        return False
    try:
        scheme, iterations_s, salt_hex, hash_hex = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if scheme != SCHEME:
        return False
    try:
        iterations = int(iterations_s)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


class AdminAuth:
    """管理员会话管理（单管理员 + 内存 session 集合）。"""

    def __init__(self, admin_user: str, admin_password_hash: str):
        self.admin_user = admin_user
        self.admin_password_hash = admin_password_hash
        self._sessions: set[str] = set()

    def login(self, username: str, password: str) -> str | None:
        """校验用户名密码，成功返回新 session token，失败返回 None。"""
        if username != self.admin_user:
            return None
        if not verify_password(password, self.admin_password_hash):
            return None
        token = secrets.token_urlsafe(32)
        self._sessions.add(token)
        return token

    def check_token(self, token: str | None) -> bool:
        """校验请求携带的 admin token 是否有效。"""
        return bool(token and token in self._sessions)

    def logout(self, token: str) -> None:
        """注销 session（可选）。"""
        self._sessions.discard(token)
