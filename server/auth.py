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
import time

SCHEME = "pbkdf2"
_ITERATIONS = 100_000


def _now() -> float:
    """monotonic 时间戳（限速锁定期用）。"""
    return time.monotonic()


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
    """管理员会话管理（单管理员 + 内存 session 集合 + 登录失败限速）。

    登录失败限速（对齐 AC-M3-2）：内存 dict user→(count, lock_until)。
    第 5 次失败即锁定，第 6 次尝试直接拒绝，锁 15 分钟。
    内存态重启清零（单进程 FastAPI 可接受，不持久化）。
    """

    MAX_FAILURES = 5
    LOCK_SECONDS = 15 * 60  # 15 分钟

    def __init__(self, admin_user: str, admin_password_hash: str):
        self.admin_user = admin_user
        self.admin_password_hash = admin_password_hash
        self._sessions: set[str] = set()
        self._fails: dict[str, tuple[int, float]] = {}  # user -> (count, lock_until)

    def _is_locked(self, username: str) -> bool:
        entry = self._fails.get(username)
        if not entry:
            return False
        count, lock_until = entry
        if lock_until and lock_until > _now():
            return True
        if lock_until and lock_until <= _now():
            # 锁到期自动清零
            self._fails.pop(username, None)
            return False
        return False

    def _record_failure(self, username: str) -> None:
        count, _ = self._fails.get(username, (0, 0.0))
        count += 1
        lock_until = _now() + self.LOCK_SECONDS if count >= self.MAX_FAILURES else 0.0
        self._fails[username] = (count, lock_until)

    def login(self, username: str, password: str) -> str | None:
        """校验用户名密码，成功返回新 session token，失败返回 None。

        锁定态：未过锁定期直接拒绝（不计次数）；锁到期后清零重计。
        """
        if self._is_locked(username):
            return None
        if username != self.admin_user:
            self._record_failure(username)
            return None
        if not verify_password(password, self.admin_password_hash):
            self._record_failure(username)
            return None
        # 登录成功清零失败计数
        self._fails.pop(username, None)
        token = secrets.token_urlsafe(32)
        self._sessions.add(token)
        return token

    def check_token(self, token: str | None) -> bool:
        """校验请求携带的 admin token 是否有效。"""
        return bool(token and token in self._sessions)

    def logout(self, token: str) -> None:
        """注销 session（可选）。"""
        self._sessions.discard(token)

    # 测试辅助：可注入时钟/清零
    def reset_lock(self, username: str) -> None:
        """清零某用户的失败计数与锁（测试用）。"""
        self._fails.pop(username, None)
