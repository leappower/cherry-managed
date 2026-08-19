"""JJC-20260819-001 方案B：Agent 配置包 pydantic 模型（服务端）。

对齐方案 §2（包结构规范）+ §四（API 请求/响应模型）。
所有模型用于：
  - 包入库前严格校验（未知字段丢弃并 warning，见 main 输入处理）
  - OpenAPI 文档生成（管理后台/调用方可见结构）
  - push/rollback 端点请求体校验

命名空间：AgentConfig（整包）、AgentConfigMetadata、AgentBody、SkillPack、
McpEntry、ProviderEntry、Attachment、PushAgentsReq、PushJob 等。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ================= 包内子模型（方案 §2.2 - §2.5） =================

class BuildInfo(BaseModel):
    """构建兼容性留痕。"""
    platform: Optional[str] = None
    cherry_version: Optional[str] = None
    fork_version: Optional[str] = None


class AgentConfigMetadata(BaseModel):
    """方案 §2.2 metadata：包元数据（rev/sha256 由服务端计算覆盖，不可信外部值）。"""
    name: str = Field(description="Agent 唯一名（= agent.name，跨版本稳定主键）")
    version: str = Field(default="1.0.0", description="语义化版本，支持 -rc.x 后缀")
    rev: Optional[int] = Field(default=None, description="服务端版本号，入库时由服务端覆盖分配")
    description: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = Field(default=None, description="ISO8601 UTC，入库时覆盖")
    sha256: Optional[str] = Field(default=None, description="内容指纹，入库时服务端计算覆盖")
    build_info: Optional[BuildInfo] = None


class AgentConfiguration(BaseModel):
    """透传的 configuration 对象（不与 agent/metadata 嵌套，保持受管 schema 对齐）。"""
    permission_mode: Optional[str] = None
    max_turns: Optional[int] = None
    env_vars: Optional[dict[str, Any]] = None
    # 其余字段透传（用户自定义键）
    model_config = {"extra": "allow"}


class AgentBody(BaseModel):
    """方案 §2.3 CreateAgentCommand 映射（name/type/model/instructions/configuration/tools/skills）。"""
    name: str
    type: str = Field(default="claude-code")
    model: str
    description: Optional[str] = None
    instructions: str = ""
    configuration: Optional[AgentConfiguration] = None
    tools: Optional[list[str]] = None
    skills: Optional[list[str]] = Field(default=None,
                                        description="引用本包顶层 skills[] 的 id")
    accessible_paths: Optional[list[str]] = None
    model_config = {"extra": "allow"}


class SkillPack(BaseModel):
    """方案 §2.4 skill 条目。"""
    id: str
    name: Optional[str] = None
    version: Optional[str] = None
    content: Optional[str] = None
    package_url: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class McpEntry(BaseModel):
    """方案 §2.5 MCP Server 配置。"""
    name: str
    type: str = Field(default="stdio", description="stdio / sse / http")
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None
    enabled: Optional[bool] = True


class ProviderEntry(BaseModel):
    """关联模型 Provider（batchUpsert 入参；api_key 一律占位/留空，不落明文）。"""
    id: str
    type: Optional[str] = "openai"
    api_base: Optional[str] = None
    models: Optional[list[str]] = None
    api_key: Optional[str] = None


class Attachment(BaseModel):
    """Agent 关联附件（语义对齐 /v1/admin/agents/{id}/files）。"""
    name: str
    path: Optional[str] = None
    content: Optional[str] = Field(default=None, description="base64 内容")


class AgentConfig(BaseModel):
    """方案 §2.1 完整 Agent 配置包（顶层为 agent_config 对象）。"""
    metadata: AgentConfigMetadata
    agent: AgentBody
    resources: Optional[dict[str, str]] = None
    skills: Optional[list[SkillPack]] = None
    mcp: Optional[list[McpEntry]] = None
    providers: Optional[list[ProviderEntry]] = None
    attachments: Optional[list[Attachment]] = None
    model_config = {"extra": "allow"}


# ================= push / rollback 请求模型（方案 §4.3） =================

class PushAgentsReq(BaseModel):
    """POST /api/admin/push/agents 请求体。devices 与 group 二选一。"""
    agent_name: str
    devices: Optional[list[str]] = None
    group: Optional[str] = None
    target_rev: Optional[int] = None       # 缺省取 latest_rev；传历史 rev 即回滚
    if_changed: Optional[bool] = True      # 仅对 deploy_status.rev < 目标 rev 设备下发
    reason: Optional[str] = None


class RollbackReq(BaseModel):
    """POST /api/admin/agent-configs/{name}/rollback-to/{rev} 请求体。"""
    devices: Optional[list[str]] = None
    group: Optional[str] = None
    reason: Optional[str] = None
