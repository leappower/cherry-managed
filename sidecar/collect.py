"""
采集模块 (S-6/S-6b) — 数据采集与上报
========================================
SDD §3.5 usage / §2.4 agent-files 采集：

  - collect_usage : 经 ForkClient.get_usage 拉 Fork /v1/admin/usage
                    → 汇总 provider/model/tokens → 返回结构化记录，
                    供 sidecar.py 组装 usage 消息上报服务端
  - collect_agent_files : 经 ForkClient.get_agent_files 采集 Agent 工作目录，
                    **严格限定在 accessible_paths 白名单内**（防越权），
                    返回 {agent_id, files:[{path,content}]} 供上报服务端

安全语义（防越权）：
  - collect_agent_files 的每个候选路径必须属于某个 accessible_path（白名单前缀），
    白名单外的路径一律拒绝，绝不返回。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sidecar.collect")

# 采集单文件内容上限（防超大文件打爆内存/带宽）
MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB
# 单次采集文件数上限
MAX_FILES = 500


def _norm(path: str) -> str:
    """归一化路径（绝对化 + 去除尾部斜杠）。"""
    return os.path.abspath(str(path)).rstrip("/\\")


class Collector:
    """Sidecar 采集器。依赖 ForkClient 注入。"""

    def __init__(self, fork):
        self.fork = fork

    # ---- S-6: usage 采集 ----
    def collect_usage(self, usage_source_url: Optional[str] = None,
                      from_ts: Optional[str] = None,
                      to: Optional[str] = None) -> dict:
        """拉 Fork /v1/admin/usage 并汇总。

        返回：
          {
            "success": bool,
            "period": "<ISO now>",
            "records": [ {provider, model, input_tokens, output_tokens, total_tokens} ],
            "errors": [str] | None,
            "raw_count": int,
          }
        """
        # usage_source_url 仅用于配置展示；实际经 ForkClient 拉取
        try:
            raw = self.fork.get_usage(from_ts=from_ts, to=to)
        except Exception as e:  # noqa: BLE001
            logger.exception("拉取 usage 失败")
            return {"success": False, "records": [], "errors": [str(e)],
                    "raw_count": 0, "period": _now_iso()}

        records = self._summarize_usage(raw)
        return {
            "success": True,
            "period": _now_iso(),
            "records": records,
            "errors": None,
            "raw_count": len(raw) if isinstance(raw, list) else 0,
        }

    @staticmethod
    def _summarize_usage(raw) -> list[dict]:
        """把 Fork 原始 usage 记录（providerId/modelId/inputTokens/...）
        汇总为 [{provider, model, input_tokens, output_tokens, total_tokens}]。

        兼容原始字段驼峰/下划线两种形态。
        """
        out: list[dict] = []
        if not isinstance(raw, list):
            return out
        for r in raw:
            if not isinstance(r, dict):
                continue
            provider = r.get("provider") or r.get("providerId") or r.get("provider_name") or ""
            model = r.get("model") or r.get("modelId") or r.get("model_name") or ""
            # 汇总该 provider/model 的 token
            agg = {
                "provider": provider,
                "model": model,
                "input_tokens": int(r.get("input_tokens", r.get("inputTokens", 0)) or 0),
                "output_tokens": int(r.get("output_tokens", r.get("outputTokens", 0)) or 0),
                "total_tokens": int(r.get("total_tokens", r.get("totalTokens", 0)) or 0),
            }
            # 逐条累计同名 provider/model
            found = next((x for x in out
                          if x["provider"] == provider and x["model"] == model), None)
            if found is None:
                out.append(agg)
            else:
                found["input_tokens"] += agg["input_tokens"]
                found["output_tokens"] += agg["output_tokens"]
                found["total_tokens"] += agg["total_tokens"]
        return out

    # ---- S-6b: 工作目录采集（限白名单）----
    def collect_agent_files(self, agent_id: str,
                            accessible_paths: list[str]) -> dict:
        """采集 Agent 工作目录，严格限定在 accessible_paths 白名单内。

        返回：
          {
            "success": bool,
            "agent_id": str,
            "files": [ {path, content} ],
            "skipped": [ {path, reason} ],
            "errors": [str] | None,
          }
        """
        # 归一化白名单
        whitelist = [_norm(p) for p in (accessible_paths or [])]
        if not whitelist:
            return {"success": False, "agent_id": agent_id, "files": [],
                    "skipped": [], "errors": ["accessible_paths 为空，拒绝采集"]}

        files: list[dict] = []
        skipped: list[dict] = []
        try:
            listing = self.fork.get_agent_files(agent_id=agent_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("拉取 agent_files 失败")
            return {"success": False, "agent_id": agent_id, "files": [],
                    "skipped": [], "errors": [str(e)]}

        if not isinstance(listing, list):
            # 非列表：可能是错误响应
            return {"success": False, "agent_id": agent_id, "files": [],
                    "skipped": [], "errors": ["agent_files 返回非列表"]}

        for item in listing:
            if len(files) >= MAX_FILES:
                skipped.append({"path": str(item), "reason": "超过单次采集上限"})
                continue
            path = item.get("path") if isinstance(item, dict) else str(item)
            norm = _norm(path)
            if not self._in_whitelist(norm, whitelist):
                skipped.append({"path": path, "reason": "越权：不在 accessible_paths 白名单内"})
                logger.warning("拒绝采集越权路径: %s", path)
                continue
            try:
                content = self._read_file(norm)
            except Exception as e:  # noqa: BLE001
                skipped.append({"path": path, "reason": f"读取失败: {e}"})
                continue
            files.append({"path": path, "content": content})

        return {"success": True, "agent_id": agent_id, "files": files,
                "skipped": skipped, "errors": None}

    # ---- 白名单校验 ----
    @staticmethod
    def _in_whitelist(norm_path: str, whitelist: list[str]) -> bool:
        """判断归一化路径是否位于任一白名单前缀之下（含前缀本身）。"""
        for base in whitelist:
            if norm_path == base:
                return True
            if norm_path.startswith(base + os.sep):
                return True
        return False

    def _read_file(self, path: str) -> str:
        """读取文件内容，超过 MAX_FILE_BYTES 截断。"""
        p = Path(path)
        if not p.is_file():
            return ""
        size = p.stat().st_size
        with open(p, "rb") as f:
            data = f.read(min(size, MAX_FILE_BYTES))
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()
