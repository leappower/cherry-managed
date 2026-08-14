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

from fastapi import Depends, FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import feed
import auth as auth_mod
from ws_server import WSServer

logging.basicConfig(level=logging.INFO)

SERVER_DIR = Path(__file__).resolve().parent

# 加载配置
with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

DB_PATH = db.db_path_from_config(CONFIG.get("db_path", "data/managed.db"))
db.init_db(DB_PATH)

ws_server = WSServer(CONFIG, DB_PATH)

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

app = FastAPI(title="CherryStudio 企业受管版 - 服务端", version="0.2.0-a0")

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
    """派发日志。"""
    return await list_dispatch_log()


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


@app.get("/api/admin/agents", dependencies=[Depends(require_admin)])
async def admin_agents():
    """各设备 Agent 清单（透传 agent_files 聚合）。"""
    conn = db.get_conn(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT device_id, agent_id, COUNT(*) AS file_count "
        "FROM agent_files GROUP BY device_id, agent_id ORDER BY device_id, agent_id"
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


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    try:
        await ws_server.handle(websocket)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG.get("host", "0.0.0.0"), port=CONFIG.get("port", 2334))
