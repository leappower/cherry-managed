"""
Fork 管理路由客户端 — Sidecar 调用 Fork /v1/admin/*
====================================================
SDD §1.1 lib/fork_client.py + §2 管理路由：

Fork 层暴露独立管理 key（feature.api_gateway.managed_key，bearer），
Sidecar 持管理 key 调以下路由：

  - GET  /v1/admin/usage           → 读 ai_usage_record（S-6 采集）
  - GET  /v1/admin/agent-files     → 枚举/读取 Agent 工作目录（S-6b，限 accessible_paths）
  - GET  /v1/admin/agents          → list agents（S-7 对账基准）

本模块是纯 HTTP 客户端（urllib），与 cherry_client.py 的官方 API 客户端并列，
Sidecar 通过它拉取 Fork 数据，不直写 sqlite（D20：复用 AgentService/ProviderService）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class ForkError(Exception):
    """Fork 管理路由调用错误。"""


class ForkClient:
    """Fork /v1/admin/* 管理路由客户端。"""

    def __init__(self, base_url: str = "http://127.0.0.1:23333",
                 api_key: str = "", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str,
                 params: Optional[dict] = None,
                 data: Optional[dict] = None) -> Any:
        url = self.base_url + path
        if params:
            from urllib.parse import urlencode

            url += ("&" if "?" in url else "?") + urlencode(params)
        headers = {}
        if self.api_key:
            # 管理 key：bearer timing-safe 比对（SDD §2 鉴权）
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
            except Exception:  # noqa: BLE001
                pass
            raise ForkError(f"HTTP {e.code} on {method} {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise ForkError(f"连接失败 {self.base_url}: {e.reason}") from e

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._request("GET", path, params)

    # ---- S-6: usage 采集 ----
    def get_usage(self, from_ts: Optional[str] = None, to: Optional[str] = None,
                  device_id: Optional[str] = None) -> list:
        """读 Fork /v1/admin/usage。

        返回 [{providerId, modelId, inputTokens, outputTokens, totalTokens, sourceType}]
        """
        params: dict = {}
        if from_ts:
            params["from"] = from_ts
        if to:
            params["to"] = to
        if device_id:
            params["device_id"] = device_id
        resp = self._get("/v1/admin/usage", params)
        # 兼容两种返回形态：list 直接返回；dict 取 data 字段
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            data = resp.get("data", resp.get("records", []))
            return data if isinstance(data, list) else []
        return []

    # ---- S-6b: 工作目录采集 ----
    def get_agent_files(self, agent_id: Optional[str] = None,
                        path: Optional[str] = None) -> Any:
        """读 Fork /v1/admin/agent-files。

        枚举/读取 Agent 工作目录（accessible_paths 内）上下文与产出。
        返回 list（枚举）或对象（读文件）。
        """
        params: dict = {}
        if agent_id:
            params["agent_id"] = agent_id
        if path:
            params["path"] = path
        resp = self._get("/v1/admin/agent-files", params)
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            data = resp.get("data", resp.get("files", resp))
            return data if isinstance(data, list) else resp
        return resp

    # ---- S-7: 对账基准 ----
    def list_agents(self) -> list:
        """读 Fork /v1/admin/agents，作为对账基准。"""
        resp = self._get("/v1/admin/agents")
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            data = resp.get("data", [])
            return data if isinstance(data, list) else []
        return []

    # ---- S-7: 受管状态（渲染 isManaged 读取旁路表，此处供对账辅助）----
    def is_managed(self, item_id: str) -> bool:
        """查询 Fork 侧受管状态（若 Fork 提供 /v1/admin/managed/:id）。"""
        try:
            resp = self._get(f"/v1/admin/managed/{item_id}")
            if isinstance(resp, dict):
                return bool(resp.get("managed", resp.get("data", False)))
        except ForkError:
            pass
        return False
