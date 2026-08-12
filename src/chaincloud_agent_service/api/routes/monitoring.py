from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from chaincloud_agent_service.api.auth import require_authenticated_user

router = APIRouter(prefix="/monitor")


class FeishuConfigRequest(BaseModel):
    webhook_url: str = Field(..., min_length=20, max_length=2000)


@router.put("/notification/feishu")
async def configure_feishu(request: Request, body: FeishuConfigRequest,
                           authorization: str | None = Header(default=None, alias="Authorization")) -> dict[str, str]:
    user = require_authenticated_user(request, authorization)
    store = getattr(request.app.state, "monitor_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="monitoring is not enabled")
    if not body.webhook_url.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
        raise HTTPException(status_code=422, detail="invalid Feishu webhook URL")
    store.set_notification_destination(user.user_id, "feishu", body.webhook_url)
    return {"status": "success", "channel": "feishu"}
