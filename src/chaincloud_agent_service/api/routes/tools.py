"""Tool inspection routes.

This route is intended for local/user-level validation: it shows which tools are
actually registered for the current environment settings. It does not execute
any tool and does not expose secrets.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from chaincloud_agent_service.tools.registry import get_tools


router = APIRouter(prefix="/tools")


class ToolInfo(BaseModel):
    name: str
    description: str | None = None
    args_schema: dict[str, Any] | None = Field(default=None)


class ToolsResponse(BaseModel):
    count: int
    tools: list[ToolInfo]


def _schema_for_tool(tool: Any) -> dict[str, Any] | None:
    """Return a best-effort JSON schema for a LangChain StructuredTool."""
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return None

    try:
        if hasattr(args_schema, "model_json_schema"):
            return args_schema.model_json_schema()
        if hasattr(args_schema, "schema"):
            return args_schema.schema()
    except Exception:
        return None

    return None


@router.get("", response_model=ToolsResponse)
def list_tools(request: Request) -> ToolsResponse:
    settings = request.app.state.settings
    tools = get_tools(settings)

    items = [
        ToolInfo(
            name=getattr(tool, "name", tool.__class__.__name__),
            description=getattr(tool, "description", None),
            args_schema=_schema_for_tool(tool),
        )
        for tool in tools
    ]
    return ToolsResponse(count=len(items), tools=items)
