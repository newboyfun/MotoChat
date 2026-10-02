"""
会话路由 — 会话管理、历史记录、提醒、通知
从 server.py 拆分，使用 Depends 依赖注入
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.deps import require_admin, require_user

logger = logging.getLogger("motochat")
router = APIRouter()


# ===== 请求模型 =====

class CreateConversationReq(BaseModel):
    title: str = "新会话"

class CreateReminderReq(BaseModel):
    conversation_id: str = "default"
    target_time: str
    content: str


# ===== 会话管理 =====

@router.get("")
async def api_conversations(user: dict = Depends(require_admin)):
    from app.database import list_conversations
    return {"conversations": list_conversations()}


@router.post("/create")
async def api_create_conversation(req: CreateConversationReq, user: dict = Depends(require_user)):
    from app.database import create_conversation
    from app.core.deps import get_chat_service
    _chat = get_chat_service()
    cid = create_conversation(req.title, _chat.avatar_name if _chat else "MONO")
    return {"conversation_id": cid}


@router.get("/{cid}")
async def api_history(cid: str, request: Request, limit: int = Query(default=200, ge=0, le=1000), offset: int = Query(default=0, ge=0), user: dict = Depends(require_user)):
    from app.database import get_history
    if user.get("role") == "user":
        cid = user.get("conversation_id") or cid
    msgs, has_more, next_offset = get_history(cid, limit=limit, offset=offset)
    return {"messages": msgs, "has_more": has_more, "next_offset": next_offset}


# ===== 提醒管理 =====

@router.post("/reminders/create")
async def api_create_reminder(req: CreateReminderReq, user: dict = Depends(require_admin)):
    from app.core.deps import get_reminder_service
    _reminder = get_reminder_service()
    if not _reminder:
        return JSONResponse(status_code=503, content={"error": "聊天服务未就绪"})
    try:
        target_dt = datetime.fromisoformat(req.target_time)
    except Exception as e:
        logger.debug(f"Time parse failed: {e}")
        return JSONResponse(status_code=400, content={"error": "时间格式不合法"})
    rid = _reminder.add(req.conversation_id, target_dt, req.content)
    return {"reminder_id": rid}


@router.post("/reminders/{rid}/cancel")
async def api_cancel_reminder(rid: str, user: dict = Depends(require_admin)):
    from app.core.deps import get_reminder_service
    _reminder = get_reminder_service()
    if not _reminder:
        return JSONResponse(status_code=503, content={"error": "聊天服务未就绪"})
    ok = _reminder.cancel(rid)
    return {"success": ok}


# ===== 通知管理 =====

@router.get("/notifications/{cid}")
async def api_notifications(cid: str, user: dict = Depends(require_user)):
    if user.get("role") == "user":
        cid = user.get("conversation_id") or cid
    from app.database import get_unread_notifications, mark_read
    items = get_unread_notifications(cid)
    mark_read(cid)
    return {"notifications": items}
