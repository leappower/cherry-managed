"""FastAPI 服务端入口。

SDD §5：Python FastAPI + uvicorn，端口 2334，异步 WS。
- GET /healthz          → {"status":"ok"}
- GET /api/devices      → 设备注册表
- GET /api/dispatch_log → 派发日志
- GET /api/usage        → usage_agg
- GET /api/reconcile    → 对账期望清单
- POST /api/dispatch    → 派发 dispatch_agent/dispatch_provider/dispatch_skills（HTTP 驱动 WS）
- WS  /ws               → 设备长连接（注册/心跳/回执/usage/status/agent_files）

启动：python3 -m uvicorn main:app --port 2334
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import feed
import auth as auth_mod
from ws_server import WSServer
import discovery as discovery_mod

logging.basicConfig(level=logging.INFO)

SERVER_DIR = Path(__file__).resolve().parent

# 加载配置
with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

DB_PATH = db.db_path_from_config(CONFIG.get("db_path", "data/managed.db"))
db.init_db(DB_PATH)

ws_server = WSServer(CONFIG, DB_PATH)

discovery_server = discovery_mod.DiscoveryServer(CONFIG, host=CONFIG.get("host", "0.0.0.0"))

# 批次D：D-2 Web 管理后台 —— 管理员鉴权
admin_auth = auth_mod.AdminAuth(
    CONFIG.get("admin_user", "admin"),
    CONFIG.get("admin_password_hash", ""),
)


def require_admin(x_admin_token: str | None = Header(default=None)):
    """管理 API 鉴权依赖：X-Admin-Token 须为有效 session token，否则 401。"""
    if not admin_auth.check_token(x_admin_token):
        raise HTTPException(status_code=401, detail="unauthorized")
    return x_admin_token

# 批次D：E-2 自建更新通道（generic electron-updater feed）
PATCH_REPO_DIR = Path(CONFIG.get("patch_repo_dir", "patch_repo"))
if not PATCH_REPO_DIR.is_absolute():
    PATCH_REPO_DIR = SERVER_DIR / PATCH_REPO_DIR
PATCH_REPO_DIR.mkdir(parents=True, exist_ok=True)

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 批次H：UDP 局域网发现（E 扫描核心）随服务端同进程启动
    await discovery_server.start()
    try:
        yield
    finally:
        await discovery_server.stop()


app = FastAPI(title="CherryStudio 企业受管版 - 服务端", version="0.2.0-a0", lifespan=lifespan)

# 静态挂载 patch_repo/ 供 electron-updater generic provider 拉取
app.mount("/patch_repo", StaticFiles(directory=str(PATCH_REPO_DIR)), name="patch_repo")

# D-2：管理后台静态页挂载（/admin/ 默认 index.html）
ADMIN_STATIC_DIR = SERVER_DIR / "static" / "admin"
ADMIN_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/admin",
    StaticFiles(directory=str(ADMIN_STATIC_DIR), html=True),
    name="admin_static",
)


def _check_token(x_token: str | None):
    """发布 API 鉴权：x_token 须匹配 config.json 的 token。"""
    expected = CONFIG.get("token")
    if expected and (not x_token or x_token != expected):
        raise HTTPException(status_code=401, detail="unauthorized")


class ReleaseReq(BaseModel):
    version: str
    file_name: str
    size: int
    sha512: str


@app.post("/api/release/publish")
async def api_release_publish(req: ReleaseReq, x_token: str | None = Header(default=None)):
    """发布新版本安装包 → 生成/覆盖 latest.yml（带 token 鉴权）。"""
    _check_token(x_token)
    try:
        return feed.publish_release(PATCH_REPO_DIR, req.version, req.file_name,
                                    req.size, req.sha512)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/devices")
async def list_devices():
    return ws_server.registry.get_all()


@app.get("/api/dispatch_log")
async def list_dispatch_log():
    conn = db.get_conn(DB_PATH)
    rows = conn.execute(
        "SELECT request_id, device_id, type, action, status, created_at "
        "FROM dispatch_log ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/usage")
async def list_usage(device_id: str | None = None):
    return ws_server.collect.usage_for(device_id)


@app.get("/api/reconcile")
async def reconcile():
    from reconcile import ReconcileService

    svc = ReconcileService(DB_PATH, ws_server.registry, ws_server.dispatch)
    return svc.reconcile_summary()


# ---- 派发请求模型 ----
class DispatchAgentReq(BaseModel):
    device_id: str
    action: str = "create"
    agent: dict
    package_url: str | None = None
    request_id: str


class DispatchProviderReq(BaseModel):
    device_id: str
    action: str = "add"
    provider: dict
    request_id: str


class DispatchSkillsReq(BaseModel):
    device_id: str
    skills: list
    request_id: str


class FetchAgentFilesReq(BaseModel):
    device_id: str
    agent_id: str
    accessible_paths: list[str] = []
    request_id: str


# ---- JJC-20260819-001 方案B：Agent 配置包 / 推送 / 回滚请求模型 ----
class AgentConfigReq(BaseModel):
    """创建/更新 Agent 配置包（顶层 agent_config 对象）。"""
    metadata: dict
    agent: dict
    resources: dict | None = None
    skills: list | None = None
    mcp: list | None = None
    providers: list | None = None
    attachments: list | None = None


class AgentPushReq(BaseModel):
    """推送/升级/灰度/回滚主端点请求。"""
    agent_name: str
    devices: list[str] | None = None
    group: str | None = None
    target_rev: int | None = None
    if_changed: bool = False
    reason: str | None = None
    operator: str | None = None


@app.post("/api/dispatch/agent")
async def api_dispatch_agent(req: DispatchAgentReq):
    return await ws_server.dispatch.dispatch_agent(
        req.device_id, req.action, req.agent, req.package_url, req.request_id
    )


@app.post("/api/dispatch/provider")
async def api_dispatch_provider(req: DispatchProviderReq):
    return await ws_server.dispatch.dispatch_provider(
        req.device_id, req.action, req.provider, req.request_id
    )


@app.post("/api/dispatch/skills")
async def api_dispatch_skills(req: DispatchSkillsReq):
    return await ws_server.dispatch.dispatch_skills(
        req.device_id, req.skills, req.request_id
    )


@app.post("/api/fetch-agent-files")
async def api_fetch_agent_files(req: FetchAgentFilesReq):
    """S-6b 工作目录采集触发：服务端 → Sidecar 下发 fetch_agent_files。"""
    return await ws_server.dispatch.fetch_agent_files(
        req.device_id, req.agent_id, req.accessible_paths, req.request_id
    )


# ================= D-2 Web 管理后台 API（需 admin token） =================
class AdminLoginReq(BaseModel):
    username: str
    password: str


@app.post("/api/admin/login")
async def admin_login(req: AdminLoginReq):
    """管理员登录：校验用户名密码，成功发 token + 写审计，失败 401。"""
    token = admin_auth.login(req.username, req.password)
    if token is None:
        db.audit(DB_PATH, req.username, "admin_login_failed", req.username)
        raise HTTPException(status_code=401, detail="invalid credentials")
    db.audit(DB_PATH, req.username, "admin_login", req.username)
    return {"token": token, "user": req.username}


@app.post("/api/admin/logout")
async def admin_logout(token: str = Depends(require_admin)):
    """注销当前 admin session。"""
    admin_auth.logout(token)
    return {"ok": True}


def _pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    """规范化分页参数：limit 默认 100（上限 500 防扫库），offset 默认 0。"""
    limit = max(1, min(limit or 100, 500))
    offset = max(0, offset or 0)
    return limit, offset


@app.get("/api/admin/devices", dependencies=[Depends(require_admin)])
async def admin_devices(limit: int | None = None, offset: int | None = None):
    """设备列表（含在线状态/分组）+ 分页元数据（total/limit/offset）。"""
    lim, off = _pagination(limit, offset)
    all_devices = ws_server.registry.get_all()
    return {"total": len(all_devices), "limit": lim, "offset": off,
            "items": all_devices[off:off + lim]}


@app.get("/api/admin/dispatch_log", dependencies=[Depends(require_admin)])
async def admin_dispatch_log():
    """派发日志（含设备内网 IP，问题3）。"""
    conn = db.get_conn(DB_PATH)
    rows = conn.execute(
        "SELECT d.request_id, d.device_id, d.type, d.action, d.status, d.created_at,"
        "       dev.ip AS ip, dev.hostname AS hostname, dev.remark AS remark "
        "FROM dispatch_log d LEFT JOIN devices dev ON dev.device_id = d.device_id "
        "ORDER BY d.created_at DESC LIMIT 500"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/admin/usage", dependencies=[Depends(require_admin)])
async def admin_usage(device_id: str | None = None):
    """用量聚合。"""
    return ws_server.collect.usage_for(device_id)


@app.get("/api/admin/audit_log", dependencies=[Depends(require_admin)])
async def admin_audit_log(limit: int | None = None, offset: int | None = None,
                          action: str | None = None, operator: str | None = None):
    """操作审计日志（D-2 核心）+ 分页/筛选（limit/offset/action/operator）。"""
    lim, off = _pagination(limit, offset)
    conn = db.get_conn(DB_PATH)
    where, params = [], []
    if action:
        where.append("action = ?")
        params.append(action)
    if operator:
        where.append("operator = ?")
        params.append(operator)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM audit_log{where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, operator, action, target, timestamp, request_id "
        f"FROM audit_log{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [lim, off]
    ).fetchall()
    return {"total": total, "limit": lim, "offset": off,
            "items": [dict(r) for r in rows]}


@app.get("/api/admin/reconcile", dependencies=[Depends(require_admin)])
async def admin_reconcile():
    """对账汇总。"""
    return await reconcile()


@app.put("/api/admin/devices/{device_id}/remark", dependencies=[Depends(require_admin)])
async def admin_device_remark(device_id: str, body: dict):
    """设置设备备注（问题3：设备ID解析调优）。body: {"remark": "..."}。"""
    remark = (body or {}).get("remark", "")
    if not isinstance(remark, str):
        return {"ok": False, "error": "remark must be str"}
    ws_server.registry.set_remark(device_id, remark)
    db.audit(DB_PATH, "admin", "device_remark", f"{device_id}->{remark}", None)
    return {"ok": True, "device_id": device_id, "remark": remark}


@app.get("/api/admin/agents", dependencies=[Depends(require_admin)])
async def admin_agents():
    """各设备 Agent 清单（agent_files 聚合 + 设备 IP/备注，问题3）。"""
    conn = db.get_conn(DB_PATH)
    rows = conn.execute(
        "SELECT a.device_id, a.agent_id, COUNT(*) AS file_count, "
        "       dev.ip AS ip, dev.remark AS remark "
        "FROM agent_files a LEFT JOIN devices dev ON dev.device_id = a.device_id "
        "GROUP BY a.device_id, a.agent_id ORDER BY a.device_id, a.agent_id"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/admin/dispatch/agent", dependencies=[Depends(require_admin)])
async def admin_dispatch_agent(req: DispatchAgentReq):
    """管理派发 Agent（复用现有 dispatch 逻辑）。"""
    return await ws_server.dispatch.dispatch_agent(
        req.device_id, req.action, req.agent, req.package_url, req.request_id
    )


@app.post("/api/admin/dispatch/provider", dependencies=[Depends(require_admin)])
async def admin_dispatch_provider(req: DispatchProviderReq):
    """管理派发 Provider。"""
    return await ws_server.dispatch.dispatch_provider(
        req.device_id, req.action, req.provider, req.request_id
    )


@app.post("/api/admin/dispatch/skills", dependencies=[Depends(require_admin)])
async def admin_dispatch_skills(req: DispatchSkillsReq):
    """管理派发 Skills。"""
    return await ws_server.dispatch.dispatch_skills(
        req.device_id, req.skills, req.request_id
    )


# ============ JJC-20260819-001 方案B：Agent 配置化推送 + 版本管理 API ============
from agent_repo import AgentRepo, validate_pkg, version_semver_ok  # noqa: E402
import schemas  # noqa: E402

_repo = AgentRepo(DB_PATH)


@app.get("/api/admin/agent-configs", dependencies=[Depends(require_admin)])
async def admin_agent_configs():
    """AC1/AC6：配置包列表（name/rev/version/updated_at/locked）。"""
    return {"ok": True, "data": _repo.list_configs()}


@app.post("/api/admin/agent-configs", dependencies=[Depends(require_admin)],
          status_code=201)
async def admin_agent_config_create(req: AgentConfigReq,
                                    token: str = Depends(require_admin)):
    """AC1：创建配置包（rev=1），返回新版本记录。"""
    pkg = req.model_dump(exclude_none=True)
    errors = validate_pkg(pkg, require_id=False)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    ver = pkg.get("metadata", {}).get("version", "1.0.0")
    if not version_semver_ok(ver):
        raise HTTPException(status_code=400, detail=f"version 非法语义化版本: {ver}")
    if _repo.get_config(pkg["metadata"]["name"]):
        raise HTTPException(status_code=409, detail="agent 已存在，请用 PUT 更新")
    rec = _repo.create_config(pkg, admin_auth.admin_user)
    return {"ok": True, "data": rec}


@app.get("/api/admin/agent-configs/{name}", dependencies=[Depends(require_admin)])
async def admin_agent_config_get(name: str):
    """包详情（最新态 + 最新版本内容）。"""
    cfg = _repo.get_config(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail="agent 配置不存在")
    return {"ok": True, "data": cfg}


@app.get("/api/admin/agent-configs/{name}/export", dependencies=[Depends(require_admin)])
async def admin_agent_config_export(name: str):
    """AC1/导出：把配置包最新版导出为 .json 包文件（可跨服务器/跨安装包分发）。"""
    cfg = _repo.get_config(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail="agent 配置不存在")
    latest_config = cfg.get("config")
    if not latest_config:
        raise HTTPException(status_code=404, detail="配置包无内容可导出")
    pkg = {"schema": "cherry-managed.agent-config", "schema_version": 1,
           "config": latest_config}
    from fastapi.responses import JSONResponse
    from urllib.parse import quote
    fn = quote(name) + ".agent.json"
    return JSONResponse(
        content=pkg,
        headers={"Content-Disposition": f'attachment; filename*=UTF-8\'\'{fn}'})


@app.post("/api/admin/agent-configs/import", dependencies=[Depends(require_admin)],
          status_code=201)
async def admin_agent_config_import(request: Request,
                                    token: str = Depends(require_admin)):
    """AC1/导入：上传 .json 包文件内容 → 创建配置包（rev=1）。同名冲突 409。
    兼容两种 body：扁平结构 {metadata,agent,...} 或 export 产出的带壳 {schema,config:{...}}。"""
    try:
        pkg = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body 必须是合法 JSON")
    if not isinstance(pkg, dict):
        raise HTTPException(status_code=400, detail="body 必须是 JSON 对象")
    # 剥 export 带壳结构
    if "config" in pkg and isinstance(pkg["config"], dict) and \
       ("agent" in pkg["config"] or "metadata" in pkg["config"]):
        pkg = pkg["config"]
    errors = validate_pkg(pkg, require_id=False)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    ver = pkg.get("metadata", {}).get("version", "1.0.0")
    if not version_semver_ok(ver):
        raise HTTPException(status_code=400, detail=f"version 非法语义化版本: {ver}")
    name = pkg.get("metadata", {}).get("name") or (pkg.get("agent") or {}).get("name")
    if not name:
        raise HTTPException(status_code=400, detail="metadata.name 或 agent.name 必填")
    if _repo.get_config(name):
        raise HTTPException(status_code=409, detail=f"配置包「{name}」已存在，请用 PUT 更新或删除后重导")
    rec = _repo.create_config(pkg, admin_auth.admin_user)
    return {"ok": True, "data": rec}


@app.put("/api/admin/agent-configs/{name}", dependencies=[Depends(require_admin)])
async def admin_agent_config_update(name: str, req: AgentConfigReq,
                                    token: str = Depends(require_admin)):
    """AC1/AC6：更新配置 → 产生新 rev（原子递增），历史保留。"""
    if _repo.get_config(name) is None:
        raise HTTPException(status_code=404, detail="agent 配置不存在")
    pkg = req.model_dump(exclude_none=True)
    pkg.setdefault("metadata", {})["name"] = name
    errors = validate_pkg(pkg, require_id=True)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    ver = pkg.get("metadata", {}).get("version", "")
    if ver and not version_semver_ok(ver):
        raise HTTPException(status_code=400, detail=f"version 非法语义化版本: {ver}")
    rec = _repo.update_config(name, pkg, admin_auth.admin_user)
    return {"ok": True, "data": rec}


@app.delete("/api/admin/agent-configs/{name}", dependencies=[Depends(require_admin)])
async def admin_agent_config_delete(name: str,
                                     token: str = Depends(require_admin)):
    """AC1：删除配置包（含全部版本/部署状态）。锁定包 409。"""
    try:
        ok = _repo.delete_config(name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    if not ok:
        raise HTTPException(status_code=404, detail="agent 配置不存在")
    db.audit(DB_PATH, admin_auth.admin_user, "agent_config_delete", name, None)
    return {"ok": True, "data": {"deleted": name}}


@app.post("/api/admin/devices/{device_id}/agent-import", dependencies=[Depends(require_admin)])
async def admin_device_agent_import(device_id: str,
                                     req: dict | None = None,
                                     token: str = Depends(require_admin)):
    """从员工机导入 Agent 列表为配置包（方案B生产端）。

    向目标设备发 WS 指令 agent_list（带 with_skills: true）→ 等回执 → 把选中的
    agent 存为配置包。
    Body（可选）：{"agent_id": "...", "name": "..."} 指定要导入的 agent；
    不指定则返回设备上的全部 agent 清单供 UI 选择。

    JJC-20260826-001：列表分支透传完整 AgentEntity（含信封 skills，不再 6 字段
    白名单截断）；导入分支把 enabled 完整 skill 实体装入 pkg.skills、agent.skills
    引用 skillId 列表，agent 补充 mcps/knowledgeBaseIds/disabledTools/modelName，
    configuration 原样透传。存量 sidecar（不理解 with_skills）回执 agent 缺
    skills 键 → 519 ERR_SKILLS_MISSING（请升级员工端侧车），拒绝落库；
    skills 为空数组 = 合法（不报错）。
    """
    import asyncio
    from ws_server import wait_for_reply, resolve_reply

    dev = ws_server.registry.get(device_id)
    if dev is None or not dev.get("online"):
        raise HTTPException(status_code=404, detail="设备离线或不存在")
    ws = ws_server.registry.get_connection(device_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="设备 WS 连接不存在")
    rid = f"imp-{device_id[-6:]}-{_now_short()}"
    await ws.send_json({"type": "agent_list", "request_id": rid,
                        "with_skills": True})
    fut = wait_for_reply(rid, timeout=20)
    try:
        reply = await asyncio.wait_for(fut, timeout=20)
    except asyncio.TimeoutError:
        resolve_reply(rid, {})  # 防悬挂
        raise HTTPException(status_code=504, detail="设备未在 20s 内回执 agent_list")
    if not reply.get("success"):
        raise HTTPException(status_code=502, detail=f"设备拉取失败: {reply.get('error')}")
    agents = reply.get("agents") or []
    if req and req.get("agent_name"):
        want = req["agent_name"]
        pick = next((a for a in agents if a.get("name") == want or a.get("id") == want), None)
        if pick is None:
            raise HTTPException(status_code=404, detail=f"设备上未找到 agent: {want}")
        # 存量 sidecar：不理解 with_skills → 回执 agent 对象无 skills 键。
        # 返回 ERR_SKILLS_MISSING 并拒绝落库（升级员工端侧车后重试）；
        # skills 为空数组 = 合法（该 agent 未启用任何技能，不报错）。
        if "skills" not in pick or pick.get("skills") is None:
            raise HTTPException(
                status_code=519,
                detail="ERR_SKILLS_MISSING：设备侧未返回 skills 元数据壳，请升级员工端侧车后重试",
            )
        enabled = [s for s in (pick.get("skills") or []) if isinstance(s, dict)]
        skills_ent = [_skill_entity(s) for s in enabled]
        pkg = {
            "metadata": {"name": pick.get("name"), "version": "1.0.0",
                         "source_device": device_id, "source_agent_id": pick.get("id")},
            "agent": {
                "id": pick.get("id"),
                "type": pick.get("type") or "claude-code",
                "name": pick.get("name"),
                "description": pick.get("description") or "",
                "instructions": pick.get("instructions") or "",
                "model": pick.get("model") or "cherryai::qwen",
                "configuration": pick.get("configuration") or {},
                # JJC-20260826-001：完整 AgentEntity 补充字段（透传，MCP 等元数据壳）
                "mcps": pick.get("mcps") or [],
                "knowledgeBaseIds": pick.get("knowledgeBaseIds") or [],
                "disabledTools": pick.get("disabledTools") or [],
                "modelName": pick.get("modelName"),
                "skills": [s.get("id") for s in enabled if s.get("id")],
            },
            # enabled 完整 skill 实体随包入仓（validate_pkg 要求 agent.skills 引用
            # 须在 pkg.skills 存在；两侧同源自 pick，确保一致）
            "skills": skills_ent,
        }
        cfg = _repo.create_config(pkg, created_by=admin_auth.admin_user)
        return {"ok": True, "data": {"imported": True, "config": cfg,
                                      "agent": pick}}
    # 未指定：仅列出设备上的 agent（供 UI 选择）——透传完整 AgentEntity
    # （含信封 skills/MCP 等），不再 6 字段白名单截断
    return {"ok": True, "data": {"imported": False, "agents": agents}}


@app.post("/api/admin/devices/{device_id}/agent-test", dependencies=[Depends(require_admin)])
async def admin_device_agent_test(device_id: str, req: dict,
                                  token: str = Depends(require_admin)):
    """对员工机上某 agent 发测试消息（方案 B④「员工端能否正常使用」验证）。

    Body：{"agent_id": ..., "prompt": "..."} → 回执 success + reply 或 error。
    """
    import asyncio
    from ws_server import wait_for_reply

    agent_id = (req or {}).get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id 必填")
    ws = ws_server.registry.get_connection(device_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="设备离线或 WS 连接不存在")
    rid = f"test-{device_id[-6:]}-{_now_short()}"
    await ws.send_json({"type": "agent_test", "request_id": rid,
                        "agent_id": agent_id, "prompt": (req or {}).get("prompt")})
    fut = wait_for_reply(rid, timeout=30)
    try:
        reply = await asyncio.wait_for(fut, timeout=30)
    except asyncio.TimeoutError:
        resolve_reply(rid, None)
        raise HTTPException(status_code=504, detail="测试超时（30s 内无回执）")
    return {"ok": True, "data": reply}


@app.get("/api/admin/agent-configs/{name}/versions", dependencies=[Depends(require_admin)])
async def admin_agent_config_versions(name: str):
    """AC1/AC6：版本历史（rev 倒序）。"""
    if _repo.get_config(name) is None:
        raise HTTPException(status_code=404, detail="agent 配置不存在")
    return {"ok": True, "data": _repo.list_versions(name)}


@app.post("/api/admin/agent-configs/{name}/rollback-to/{rev}",
          dependencies=[Depends(require_admin)])
async def admin_agent_config_rollback(name: str, rev: int,
                                      req: schemas.RollbackReq | None = None,
                                      token: str = Depends(require_admin)):
    """AC4：回滚 —— 指定历史 rev → 向目标设备下发 → 设备恢复到该版本。

    复用 push 逻辑（target_rev=rev）。响应含各设备 request_id/dispatch 统计。
    body 可选（devices/group/reason），缺省即按该 agent 全部已部署设备。
    """
    return await _push_agents(name, AgentPushReq(
        agent_name=name, devices=(req.devices if req else None),
        group=(req.group if req else None), target_rev=rev,
        reason=(req.reason if req and req.reason else "rollback"),
    ), token)


async def _push_agents(name: str, req: AgentPushReq, token: str):
    """推送/升级/灰度/回滚主逻辑（AC2/AC4）。"""
    cfg = _repo.get_config(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail="agent 配置不存在")
    if _repo.is_locked(name):
        raise HTTPException(status_code=400, detail="agent 已锁定（灰度/维护期禁推）")
    # 目标 rev：缺省取最新；指定历史 rev 即回滚
    target_rev = req.target_rev or cfg["latest_rev"]
    version_rec = _repo.get_version(name, target_rev)
    if version_rec is None:
        raise HTTPException(status_code=404, detail=f"rev {target_rev} 不存在")
    pkg = version_rec["config"]
    # 目标设备：devices 或 group 二选一
    targets = _resolve_push_targets(req)
    if not targets:
        raise HTTPException(status_code=400, detail="未指定目标设备（devices 或 group）")
    operator = req.operator or admin_auth.admin_user
    dispatched = []
    sent = queued = skipped = 0
    for device_id in targets:
        # if_changed：仅对 deploy_status.rev < target_rev 的设备下发
        if req.if_changed:
            dep = _repo.get_deploy(device_id, name)
            if dep and dep["rev"] >= target_rev:
                skipped += 1
                continue
        rid = f"req-{name}-{target_rev}-{_now_short()}"
        res = await ws_server.dispatch.dispatch_agent_config(
            device_id, pkg.get("agent", {}), pkg.get("metadata", {}),
            pkg.get("resources"), pkg.get("skills") or [], None, rid,
        )
        dispatched.append(rid)
        sent += 1 if res["online"] else 0
        queued += 0 if res["online"] else 1
    db.audit(DB_PATH, operator, "agent_push", f"{name}:rev{target_rev}->{len(targets)}dev", None)
    return {"ok": True, "data": {
        "request_ids": dispatched, "agent_name": name, "target_rev": target_rev,
        "dispatch": {"total": len(dispatched), "online_sent": sent,
                      "offline_queued": queued, "skipped_unchanged": skipped}}}


def _resolve_push_targets(req: AgentPushReq) -> list[str]:
    """解析推送目标：devices 列表优先，否则按 group 匹配。"""
    if req.devices:
        return req.devices
    if req.group:
        all_dev = ws_server.registry.get_all()
        return [d["device_id"] for d in all_dev if d.get("group") == req.group]
    return []


def _now_short() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")[:15]


# InstalledSkillSchema 字段（Fork 源码钉死，无 content）；源实体缺键时置 None，
# 保证去掉 content 后形如 {'id','name','description','folderName','source',
# 'sourceUrl','namespace','author','version','sourceTags','contentHash'}
_SKILL_ENTITY_KEYS = (
    "id", "name", "description", "folderName", "source", "sourceUrl",
    "namespace", "author", "version", "sourceTags", "contentHash",
)


def _skill_entity(s: dict) -> dict:
    """从 sidecar 回执的 enabled skill 实体提取 InstalledSkillSchema 字段（驼峰，无 content）。

    只拷贝已声明字段，不引入任何额外键（与编辑表单彻底隔离，避免 extra allow 静默丢弃）。
    """
    out = {k: s.get(k) for k in _SKILL_ENTITY_KEYS}
    # 剔除 None 值字段（保持与 InstalledSkillSchema 可选字段语义一致；
    # 避免 {"field": null} 进入 compute_sha256/落盘/推送载荷）
    return {k: v for k, v in out.items() if v is not None}


@app.post("/api/admin/push/agents", dependencies=[Depends(require_admin)])
async def admin_push_agents(req: AgentPushReq, token: str = Depends(require_admin)):
    """推送/升级/灰度/回滚主端点（AC2/AC4）。"""
    return await _push_agents(req.agent_name, req, token)


@app.get("/api/admin/push/jobs", dependencies=[Depends(require_admin)])
async def admin_push_jobs(request_id: str | None = None):
    """推送任务列表/单任务详情（含每设备回执状态）。"""
    conn = db.get_conn(DB_PATH)
    if request_id:
        row = conn.execute(
            "SELECT request_id, device_id, type, action, status, created_at "
            "FROM dispatch_log WHERE request_id=?", (request_id,),
        ).fetchone()
        return {"ok": True, "data": dict(row) if row else None}
    rows = conn.execute(
        "SELECT request_id, device_id, type, action, status, created_at "
        "FROM dispatch_log WHERE action='update' OR type='dispatch_agent' "
        "ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    try:
        await ws_server.handle(websocket)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG.get("host", "0.0.0.0"), port=CONFIG.get("port", 2334))
