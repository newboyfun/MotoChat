"""
配置路由 — 配置管理、统计、日志、Token用量、用户API配置
从 server.py 拆分，使用 Depends 依赖注入
"""
import os
import logging
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.deps import require_admin, require_user
from app.core.utils import mask_key, json_utf8_response

logger = logging.getLogger("motochat")
router = APIRouter()


# ===== 请求模型 =====

class UserApiConfigReq(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


# ===== 配置管理 =====

@router.get("/config")
async def api_config_get(request: Request, user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    if user.get("role") != "admin":
        persona_prompt = ""
        for item in data.get('web', {}).get('users') or []:
            if item.get("username") == user["username"]:
                persona_prompt = item.get("persona_prompt") or ""
                break
        # 只回角色名，不回服务器绝对路径（避免把本机目录结构暴露给普通用户）
        return json_utf8_response({
            "user_persona": os.path.basename(data.get("behavior", {}).get("avatar_dir", "") or ""),
            "persona_prompt": persona_prompt,
        })
    llm = data.get('llm', {})
    vision = data.get('vision', {})
    tts = data.get('tts', {})
    masked = mask_key(llm.get("api_key") or "")
    # 注意：不下发明文 api_key，查看需走 /api/auth/reveal-key（密码确认）
    return json_utf8_response({
        "api_key_masked": masked,
        "base_url": llm.get("base_url", ""),
        "model": llm.get("model", ""),
        "temperature": llm.get("temperature", 0),
        "max_tokens": llm.get("max_tokens", 0),
        "max_context_rounds": llm.get("max_context_rounds", 0),
        "vision_api_key": mask_key(vision.get("api_key", "")),
        "vision_base_url": vision.get("base_url", ""),
        "vision_model": vision.get("model", ""),
        "admin_persona_override": data.get('web', {}).get('admin', {}).get('admin_persona_override', {}),
        "tts_api_key": mask_key(tts.get("api_key", "")),
        "tts_base_url": tts.get("base_url", ""),
        "tts_model_id": tts.get("model_id", ""),
    })


@router.post("/config/save")
async def api_config_save(req: dict, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    # 白名单 + 类型校验，防止注入任意路径值
    _ALLOWED = {
        "llm.api_key": str, "llm.base_url": str, "llm.model": str,
        "llm.max_tokens": int, "llm.temperature": (int, float),
        "llm.max_context_rounds": int, "llm.auto_model_switch": bool,
        "vision.api_key": str, "vision.base_url": str, "vision.model": str,
        "vision.temperature": (int, float),
        "tts.api_key": str, "tts.model_id": str, "tts.base_url": str,
        "behavior.avatar_dir": str,
        "behavior.auto_message.content": str,
        "behavior.auto_message.min_hours": (int, float),
        "behavior.auto_message.max_hours": (int, float),
        "behavior.quiet_time.start": str, "behavior.quiet_time.end": str,
        "behavior.queue_timeout": int,
    }
    try:
        # 先整体校验所有字段（任一项不合法则一个都不写），再一次性落盘。
        # 逐个 update(path, value) 会在第 k 个字段非法时留下前面已写入的半成品配置。
        pairs = []
        for path, value in req.items():
            if path not in _ALLOWED:
                return JSONResponse(status_code=400, content={"error": f"不允许修改 {path}"})
            expected = _ALLOWED[path]
            if not isinstance(value, expected):
                return JSONResponse(status_code=400, content={"error": f"{path} 类型错误，期望 {expected}"})
            # 拒绝把前端回显的掩码写回真实 key（防止误覆盖）
            if path.endswith(".api_key") and isinstance(value, str) and "***" in value:
                return JSONResponse(status_code=400, content={"error": f"{path} 为掩码值，已拒绝保存；如需修改请输入完整 key"})
            pairs.append((path, value))
        _get_cfg().update_many(pairs)
        return {"success": True, "updated": len(pairs)}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})


# ===== 统计和日志 =====

@router.get("/stats")
async def api_stats(user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    from app.database import get_message_count
    from app.core.deps import get_chat_service, get_reminder_service
    from app.core.utils import ROOT
    _chat = get_chat_service()
    _reminder = get_reminder_service()
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    persona_count = 0
    try:
        persona_count = len([n for n in os.listdir(avatar_root) if os.path.isdir(os.path.join(avatar_root, n)) and os.path.isfile(os.path.join(avatar_root, n, "avatar.md"))])
    except Exception as e:
        logger.debug(f"获取角色数据失败: {e}")
    api_key = ""
    if _chat and hasattr(_chat, 'llm') and _chat.llm:
        api_key = getattr(_chat.llm, 'api_key', '')
    return {
        "active_persona": _chat.avatar_name if _chat else "-",
        "persona_count": persona_count,
        "total_messages": get_message_count(),
        "api_key_configured": bool(api_key),
        "reminder_count": len(_reminder.list_all()) if _reminder else 0,
    }


@router.get("/logs")
async def api_logs(level: str = Query(default="all"), limit: int = Query(default=100), user: dict = Depends(require_admin)):
    from app.server import get_log_buffer
    logs = list(get_log_buffer())
    if level and level != "all":
        logs = [x for x in logs if x.get("level") == level]
    return {"logs": logs[-limit:]}


@router.get("/token-usage")
async def api_token_usage(user: dict = Depends(require_user)):
    """获取当前用户的 token 用量（累计）"""
    from app.database import get_user_token_usage
    usage = get_user_token_usage(user.get('username', ''))
    return json_utf8_response({"usage": usage})


@router.get("/admin/token-usage")
async def api_admin_token_usage(user: dict = Depends(require_admin)):
    """获取所有用户的 token 用量（管理员用）"""
    from app.database import get_all_token_usage
    all_usage = get_all_token_usage()
    # 计算总计
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "request_count": 0}
    for u in all_usage:
        total["prompt_tokens"] += u["prompt_tokens"]
        total["completion_tokens"] += u["completion_tokens"]
        total["total_tokens"] += u["total_tokens"]
        total["request_count"] += u["request_count"]
    return json_utf8_response({"users": all_usage, "total": total})


# ===== 用户API配置 =====

@router.get("/user/api-config")
async def api_user_api_config(user: dict = Depends(require_user)):
    """用户获取自己的 API 配置"""
    from data.config import get_config as _get_cfg
    from app.core.utils import find_user_config
    user_config = find_user_config(user.get('username'))
    if user_config:
        api_config = user_config.get('api_config') or {}
        api_key = api_config.get('api_key', '')
        # 返回掩码后的 key，避免前端日志/缓存泄露完整密钥（统一用 mask_key，只露末 4 位）
        masked_key = mask_key(api_key)
        return {
            "api_key": masked_key,
            "api_key_masked": True,
            "base_url": api_config.get('base_url', ''),
            "model": api_config.get('model', ''),
            "has_custom_api": bool(api_key)
        }
    return {"api_key": "", "api_key_masked": True, "base_url": "", "model": "", "has_custom_api": False}


@router.post("/user/api-config")
async def api_user_api_config_update(req: UserApiConfigReq, user: dict = Depends(require_user)):
    """用户更新自己的 API 配置"""
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    if user.get('role') == 'admin':
        admin = data.setdefault('web', {}).setdefault('admin', {})
        if req.api_key or req.base_url or req.model:
            # 拒绝掩码回写：前端回显的掩码 key 不能覆盖真实 key
            if '***' in req.api_key:
                return JSONResponse(status_code=400, content={"error": "api_key 为掩码值，请输入完整 key"})
            # key 留空时保留原 key，允许只改 base_url/model
            _old = admin.get('api_config') or {}
            admin['api_config'] = {"api_key": req.api_key or _old.get('api_key', ''), "base_url": req.base_url, "model": req.model}
        else:
            admin.pop('api_config', None)
        _get_cfg().update('web.admin', data['web']['admin'])
    else:
        users = data.get('web', {}).get('users') or []
        for item in users:
            if item.get('username') == user['username']:
                if req.api_key or req.base_url or req.model:
                    if '***' in req.api_key:
                        return JSONResponse(status_code=400, content={"error": "api_key 为掩码值，请输入完整 key"})
                    # key 留空时保留原 key，允许只改 base_url/model
                    _old = item.get('api_config') or {}
                    item['api_config'] = {"api_key": req.api_key or _old.get('api_key', ''), "base_url": req.base_url, "model": req.model}
                else:
                    item.pop('api_config', None)
                break
        _get_cfg().update('web.users', users)
    return {"success": True}


@router.get("/user/home-stats")
async def api_user_home_stats(user: dict = Depends(require_user)):
    from app.database import get_message_count_for_conversation
    from app.core.deps import get_chat_service
    _chat = get_chat_service()
    conversation_id = user.get("conversation_id")
    return json_utf8_response({
        "conversation_id": conversation_id or "",
        "persona": user.get("persona") or (_chat.avatar_name if _chat else ""),
        "message_count": get_message_count_for_conversation(conversation_id) if conversation_id else 0,
    })
