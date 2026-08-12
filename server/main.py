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

from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import feed
from ws_server import WSServer

logging.basicConfig(level=logging.INFO)

SERVER_DIR = Path(__file__).resolve().parent

# 加载配置
with open(SERVER_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

DB_PATH = db.db_path_from_config(CONFIG.get("db_path", "data/managed.db"))
db.init_db(DB_PATH)

ws_server = WSServer(CONFIG, DB_PATH)

# 批次D：E-2 自建更新通道（generic electron-updater feed）
PATCH_REPO_DIR = Path(CONFIG.get("patch_repo_dir", "patch_repo"))
if not PATCH_REPO_DIR.is_absolute():
    PATCH_REPO_DIR = SERVER_DIR / PATCH_REPO_DIR
PATCH_REPO_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CherryStudio 企业受管版 - 服务端", version="0.2.0-a0")

# 静态挂载 patch_repo/ 供 electron-updater generic provider 拉取
app.mount("/patch_repo", StaticFiles(directory=str(PATCH_REPO_DIR)), name="patch_repo")


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


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    try:
        await ws_server.handle(websocket)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=CONFIG.get("host", "0.0.0.0"), port=CONFIG.get("port", 2334))
