"""
CherryStudio 客户端库 — 封装官方/受管 API Gateway (23333)
=========================================================
用途: Sidecar 通过本模块与本地/远程 CherryStudio API 交互。
基座: 基于已实测的官方 API 能力（/v1/agents, /v1/models, /v1/knowledge-bases）。

JJC-20260819-001 方案B：新增「受管模式」(managed=True 默认)。
受管版 CherryStudio 没有官方 /v1/* 通用路由（/v1/agents 返回 404），有效路由是
/v1/admin/agents*；且鉴权只认 ``Authorization: Bearer <受管管理 key>`` 单头
（不再发 x-api-key，双侧头会被受管版拒绝）。sidecar 所有 Agent/Skill/MCP
写操作均走受管模式。

用法示例:
    from cherry_client import CherryClient
    c = CherryClient(host="127.0.0.1", port=23333, api_key="KEY", managed=True)
    agents = c.list_agents()
    models = c.list_models()
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any, Optional

logger = logging.getLogger("cherry_client")


class CherryError(Exception):
    """CherryStudio API 调用错误。"""


class CherryClient:
    """CherryStudio 官方 API 客户端。

    managed=True（默认，对齐当前受管部署形态）：
      - 所有 /v1/xxx 路径映射为 /v1/admin/xxx（受管端点前缀）
      - 鉴权只发 ``Authorization: Bearer``，不发 x-api-key（受管版只认 Bearer）
      - api_key 为空时告警（受管模式 /v1/admin/* 会 401/403）
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 23333,
                 api_key: str = "", timeout: float = 10.0,
                 managed: bool = True):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.timeout = timeout
        self.managed = managed
        self.base_url = f"http://{host}:{port}"
        self._admin = "/v1/admin"       # 受管端点前缀

    @classmethod
    def from_machine(cls, machine: dict, timeout: float = 10.0) -> "CherryClient":
        """从 list.json 的 machine 条目构建客户端（避免脚本内出现 key 字面量）。"""
        return cls(
            host=machine["ip"],
            port=machine.get("port", 23333),
            api_key=machine.get("api_key", ""),
            timeout=timeout,
        )

    def _path(self, p: str) -> str:
        """受管模式把 /v1/xxx 映射为 /v1/admin/xxx。"""
        if self.managed and p.startswith("/v1/"):
            return self._admin + p[len("/v1"):]
        return p

    # ── 底层请求 ──────────────────────────────────────────────
    def _request(self, method: str, path: str,
                 data: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{self._path(path)}"
        headers = {}
        if self.api_key:
            # 受管版只认 Bearer；去掉 x-api-key（双侧头被拒）
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.managed:
            # 受管模式强制要求 key：缺失时告警而非静默 401/403
            logger.warning("受管模式缺 managed_key(api_key 为空), %s 将 401/403", url)
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
            raise CherryError(f"HTTP {e.code} on {method} {self._path(path)}: {detail}") from e
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

    # ── Agent 管理（受管 API：/v1/admin/agents）────────────────
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
        """部分更新 Agent（如改 instructions/configuration）——单字段热修。"""
        return self._patch(f"/v1/agents/{agent_id}", payload)

    def put_agent(self, agent_id: str, payload: dict) -> dict:
        """全量更新 Agent（升级/回滚主通道，P1 约定 update 无条件走此）。"""
        return self._request("PUT", f"/v1/agents/{agent_id}", payload)

    def delete_agent(self, agent_id: str) -> dict:
        return self._delete(f"/v1/agents/{agent_id}")

    # ── Agent 会话 ────────────────────────────────────────────
    def create_agent_session(self, agent_id: str, payload: Optional[dict] = None) -> dict:
        return self._post(f"/v1/agents/{agent_id}/sessions", payload or {})

    def send_agent_message(self, agent_id: str, session_id: str,
                           content: str, **kwargs) -> dict:
        payload = {"content": content, **kwargs}
        return self._post(f"/v1/agents/{agent_id}/sessions/{session_id}/messages", payload)

    # ── 模型查询（只读）───────────────────────────────────────
    def list_models(self) -> list:
        """列出所有可用模型。返回 [{id, name, provider, provider_name, ...}]"""
        resp = self._get("/v1/models")
        return resp.get("data", []) if isinstance(resp, dict) else []

    # ── 知识库（只读）─────────────────────────────────────────
    def list_knowledge_bases(self) -> list:
        resp = self._get("/v1/knowledge-bases")
        return resp.get("data", []) if isinstance(resp, dict) else []

    # ── Skill 管理（受管 API：/v1/admin/skills）────────────────
    def list_skills(self) -> list:
        """列出所有 Skill。返回 [{id, name, version, ...}]"""
        resp = self._get("/v1/skills")
        return resp.get("data", []) if isinstance(resp, dict) else []

    def create_skill(self, payload: dict) -> dict:
        """创建/更新 Skill（upsert 语义由受管版实现）。"""
        return self._post("/v1/skills", payload)

    # ── MCP Server 管理（受管 API：/v1/admin/mcp）──────────────
    def list_mcp(self) -> list:
        """列出所有 MCP Server。返回 [{id, name, type, ...}]"""
        resp = self._get("/v1/mcp")
        return resp.get("data", []) if isinstance(resp, dict) else []

    def put_mcp(self, mcp_id: str, payload: dict) -> dict:
        """全量写入/更新 MCP Server。"""
        return self._request("PUT", f"/v1/mcp/{mcp_id}", payload)

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
