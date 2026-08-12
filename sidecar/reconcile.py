"""
对账模块 (S-7) — 服务端期望 vs 本地受管清单比对
===================================================
SDD §8.5 / §9：Sidecar 周期性对账（缺的补 / 受管保护 / 非受管忽略）。

语义：
  - 受管项（在 ManagedRegistry 旁路表登记过）按服务端期望修复（补/改）。
  - 非受管项（员工自配，不在旁路表）**忽略不碰**，绝不因对账误删/误改。

reconcile(server_expected, local_registry) → 结构化差异清单：
  {
    "missing":      [ {kind, id, expected} ],   # 期望有但本地没有 → 需补
    "modified":     [ {kind, id, expected, local} ],  # 受管但内容不一致 → 需修
    "extra_unmanaged":[ {kind, id} ],           # 本地有但不在期望，且非受管 → 忽略（不处理）
    "managed_extra":[ {kind, id} ],             # 本地受管但不在期望 → 需回收（提示删除）
  }
"""
from __future__ import annotations

import logging

logger = logging.getLogger("sidecar.reconcile")


def _identity(item: dict) -> str:
    """从清单条目提取稳定 id。"""
    return item.get("id") or item.get("name", "")


class ReconcileResult:
    """对账结果容器。"""

    def __init__(self):
        self.missing: list[dict] = []          # 需补
        self.modified: list[dict] = []         # 受管但需修
        self.extra_unmanaged: list[dict] = []  # 非受管多余 → 忽略
        self.managed_extra: list[dict] = []    # 受管但期望中不存在 → 需回收

    def to_dict(self) -> dict:
        return {
            "missing": self.missing,
            "modified": self.modified,
            "extra_unmanaged": self.extra_unmanaged,
            "managed_extra": self.managed_extra,
            "summary": {
                "missing": len(self.missing),
                "modified": len(self.modified),
                "extra_unmanaged": len(self.extra_unmanaged),
                "managed_extra": len(self.managed_extra),
            },
        }


class ReconcileEngine:
    """对账引擎。依赖 ManagedRegistry 注入（判定受管/非受管）。"""

    def __init__(self, registry):
        self.registry = registry

    def reconcile(self, server_expected: dict | list,
                  local_registry=None) -> dict:
        """比对服务端期望清单 vs 本地受管项。

        server_expected 支持两种形态：
          - list[ {kind,id,name,...} ]（推荐）
          - dict 含 "expected_dispatches"/"expected_agents" 等键
        local_registry 可为 None（此时用构造注入的 registry）。
        """
        reg = local_registry or self.registry
        result = ReconcileResult()

        expected = self._normalize_expected(server_expected)
        # 本地受管清单：{kind: {id: {kind,id,...}}}
        local = self._local_managed(reg)

        # 按 kind 归组期望
        for item in expected:
            kind = item.get("kind", "agent")
            eid = _identity(item)
            if not eid:
                continue
            local_item = local.get(kind, {}).get(eid)
            if local_item is None:
                # 期望有但本地无受管记录 → 需补
                result.missing.append({"kind": kind, "id": eid, "expected": item})
            else:
                # 已受管：比对内容签名，不一致 → 需修
                if self._signature(item) != local_item.get("signature"):
                    result.modified.append({
                        "kind": kind, "id": eid,
                        "expected": item, "local": local_item,
                    })

        # 本地受管但期望中不存在 → 需回收
        expected_ids = {(item.get("kind", "agent"), _identity(item)) for item in expected}
        for kind, items in local.items():
            for eid in items:
                if (kind, eid) not in expected_ids:
                    result.managed_extra.append({"kind": kind, "id": eid})

        return result.to_dict()

    # ---- 归一化 ----
    @staticmethod
    def _normalize_expected(server_expected) -> list[dict]:
        if isinstance(server_expected, list):
            return [i for i in server_expected if isinstance(i, dict)]
        if isinstance(server_expected, dict):
            # 兼容多键：取任一含 agent/provider/skill 的清单
            for key in ("expected_agents", "expected_dispatches",
                        "expected_providers", "expected_skills", "expected"):
                val = server_expected.get(key)
                if isinstance(val, list):
                    return [i for i in val if isinstance(i, dict)]
            # 直接含 id/kind 的单条目
            if server_expected.get("id"):
                return [server_expected]
        return []

    # ---- 本地受管清单 ----
    def _local_managed(self, reg) -> dict:
        """读取旁路表全部受管项，附加内容签名。

        返回 {kind: {id: {"kind","id","signature","created_at"}}}
        """
        out: dict = {}
        try:
            rows = reg.all() if hasattr(reg, "all") else []
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            kind = row.get("kind")
            rid = row.get("id")
            if not kind or not rid:
                continue
            out.setdefault(kind, {})[rid] = {
                "kind": kind,
                "id": rid,
                "created_at": row.get("created_at"),
                # 旁路表只记受管标记，无内容；签名默认来自 id 本身
                "signature": f"{kind}:{rid}",
            }
        return out

    @staticmethod
    def _signature(item: dict) -> str:
        """计算期望条目内容签名（后续可扩展为哈希/版本）。"""
        # 用 id + 可选 version 作为签名基准
        ver = item.get("version")
        return f"{item.get('kind', 'agent')}:{_identity(item)}" + (f":{ver}" if ver else "")


def reconcile(server_expected, local_registry):
    """便捷函数：默认构造引擎并执行对账。local_registry 需支持 all()/is_managed()。"""
    engine = ReconcileEngine(local_registry)
    return engine.reconcile(server_expected, local_registry)
