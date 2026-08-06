"""
CherryStudio 客户端库 — 封装官方 API Gateway (23333)
======================================================
用途: Sidecar 通过本模块与本地/远程 CherryStudio 官方 API 交互。
基座: 基于已实测的官方 API 能力（/v1/agents, /v1/models, /v1/knowledge-bases）。
注意: /v1/providers 官方不存在(404)，模型统一管控需走 Fork 或直写数据目录。

用法示例:
    from cherry_client import CherryClient
    c = CherryClient(host="127.0.0.1", port=23333, api_key="cs-sk-xxx")
    agents = c.list_agents()
    models = c.list_models()
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any, Optional


class CherryError(Exception):
    """CherryStudio API 调用错误。"""


class CherryClient:
    """CherryStudio 官方 API 客户端。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 23333,
                 api_key: str = "", timeout: float = 10.0):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = f"http://{host}:{port}"

    @classmethod
    def from_machine(cls, machine: dict, timeout: float = 10.0) -> "CherryClient":
        """从 list.json 的 machine 条目构建客户端（避免脚本内出现 key 字面量）。"""
        return cls(
            host=machine["ip"],
            port=machine.get("port", 23333),
            api_key=machine.get("api_key", ""),
            timeout=timeout,
        )

    # ── 底层请求 ──────────────────────────────────────────────
    def _request(self, method: str, path: str,
                 data: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = {}
        if self.api_key:
            # CherryStudio 支持 x-api-key 与 Bearer 两种方言
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["x-api-key"] = self.api_key
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                pass
            raise CherryError(f"HTTP {e.code} on {method} {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise CherryError(f"连接失败 {self.base_url}: {e.reason}") from e

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, data: dict) -> Any:
        return self._request("POST", path, data)

    def _patch(self, path: str, data: dict) -> Any:
        return self._request("PATCH", path, data)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ── 健康检查 ──────────────────────────────────────────────
    def health(self) -> dict:
        return self._get("/health")

    # ── Agent 管理 (官方 API ✓) ───────────────────────────────
    def list_agents(self) -> list:
        """列出所有 Agent。返回 [{id, name, type, ...}]"""
        resp = self._get("/v1/agents")
        return resp.get("data", []) if isinstance(resp, dict) else []

    def get_agent(self, agent_id: str) -> dict:
        return self._get(f"/v1/agents/{agent_id}")

    def create_agent(self, payload: dict) -> dict:
        """创建 Agent。payload 需含 type/name/model/instructions/configuration 等。"""
        return self._post("/v1/agents", payload)

    def patch_agent(self, agent_id: str, payload: dict) -> dict:
        """部分更新 Agent（如改 instructions/configuration）。"""
        return self._patch(f"/v1/agents/{agent_id}", payload)

    def put_agent(self, agent_id: str, payload: dict) -> dict:
        """全量更新 Agent。"""
        return self._request("PUT", f"/v1/agents/{agent_id}", payload)

    def delete_agent(self, agent_id: str) -> dict:
        return self._delete(f"/v1/agents/{agent_id}")

    # ── Agent 会话 (官方 API ✓) ───────────────────────────────
    def create_agent_session(self, agent_id: str, payload: Optional[dict] = None) -> dict:
        return self._post(f"/v1/agents/{agent_id}/sessions", payload or {})

    def send_agent_message(self, agent_id: str, session_id: str,
                           content: str, **kwargs) -> dict:
        payload = {"content": content, **kwargs}
        return self._post(f"/v1/agents/{agent_id}/sessions/{session_id}/messages", payload)

    # ── 模型查询 (官方 API ✓, 只读) ───────────────────────────
    def list_models(self) -> list:
        """列出所有可用模型。返回 [{id, name, provider, provider_name, ...}]"""
        resp = self._get("/v1/models")
        return resp.get("data", []) if isinstance(resp, dict) else []

    # ── 知识库 (官方 API ✓) ───────────────────────────────────
    def list_knowledge_bases(self) -> list:
        resp = self._get("/v1/knowledge-bases")
        return resp.get("data", []) if isinstance(resp, dict) else []

    # ── 便捷: 按名前查 Agent/模型 ─────────────────────────────
    def find_agent_by_name(self, name: str) -> Optional[dict]:
        for a in self.list_agents():
            if a.get("name") == name:
                return a
        return None

    def find_model_by_name(self, name: str) -> Optional[dict]:
        for m in self.list_models():
            if m.get("name") == name:
                return m
        return None
