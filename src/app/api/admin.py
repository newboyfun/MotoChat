"""
管理员路由 — 用户管理、性格库、API配置
从 server.py 拆分，使用 Depends 依赖注入
"""
import os
import re
import logging
from typing import Optional
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.deps import require_admin, _auth_sessions, _auth_lock
from app.core.utils import ROOT, safe_persona_name, mask_key, json_utf8_response

logger = logging.getLogger("motochat")
router = APIRouter()


# ===== 请求模型 =====

class CreateUserReq(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=4, max_length=128)
    persona: str = "MONO"

class UpdateUserReq(BaseModel):
    username: str
    password: Optional[str] = Field(None, min_length=4, max_length=128)
    persona: Optional[str] = None
    persona_prompt: Optional[str] = None
    persona_override: Optional[dict] = None
    library_access: Optional[bool] = None
    allowed_personas: Optional[list] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None

class ResetPasswordReq(BaseModel):
    username: str
    new_password: str = Field(min_length=4, max_length=128)

class SetPersonaReq(BaseModel):
    username: str
    persona: str

class PersonaOverrideReq(BaseModel):
    persona: str
    content: str = ""

class PersonaLibraryReq(BaseModel):
    name: str
    content: str = ""
    persona_name: str = ""
    public: bool = True

class DeleteLibraryReq(BaseModel):
    name: str

class DeleteUserReq(BaseModel):
    username: str

class SaveUserPersonaContentReq(BaseModel):
    content: str = ""


# ===== 用户管理 =====

@router.get("/users")
async def api_admin_users(user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw().get('web', {})
    users = []
    for item in data.get('users') or []:
        users.append({
            "username": item.get("username"),
            "role": item.get("role", "user"),
            "persona": item.get("persona") or "MONO",
            "conversation_id": item.get("conversation_id") or f"user_{item.get('username')}",
            "has_custom_persona_prompt": bool(item.get("persona_prompt")),
            "persona_prompt": item.get("persona_prompt") or "",
            "persona_override": item.get("persona_override") or {},
            "pending_persona_override": item.get("pending_persona_override") or {},
            "library_access": item.get("library_access", False),
            "allowed_personas": item.get("allowed_personas"),
            "user_personas": item.get("user_personas") or {},
        })
    return {"users": users}


@router.post("/users/create")
async def api_admin_users_create(req: CreateUserReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    from data.config import hash_password as _hpw
    # 用户名会成为记忆目录路径组件（avatars/{persona}/memory/user_{username}），必须白名单校验
    if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}", req.username):
        return JSONResponse(status_code=400, content={"error": "用户名只能包含字母、数字、下划线、中划线和中文"})
    data = _get_cfg().get_raw()
    users = data.get('web', {}).get('users') or []
    for item in users:
        if item.get('username') == req.username:
            return JSONResponse(status_code=400, content={"error": "用户名已存在"})
    users.append({
        "username": req.username,
        "password": _hpw(req.password),
        "role": "user",
        "persona": req.persona or "MONO",
        "conversation_id": f"user_{req.username}",
        "persona_override": {},
        "persona_prompt": "",
        "library_access": False,
        "allowed_personas": None,
    })
    _get_cfg().update('web.users', users)
    return {"success": True}


@router.post("/users/delete")
async def api_admin_users_delete(req: DeleteUserReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    username = req.username.strip()
    if not username:
        return JSONResponse(status_code=400, content={"error": "内容不能为空"})
    data = _get_cfg().get_raw()
    users = data.get('web', {}).get('users') or []
    target = next((x for x in users if x.get('username') == username), None)
    new_users = [x for x in users if x.get('username') != username]
    if len(new_users) == len(users):
        return JSONResponse(status_code=404, content={"error": "操作失败"})
    _get_cfg().update('web.users', new_users)
    # 清除被删除用户的所有 session
    with _auth_lock:
        tokens_to_remove = [t for t, s in _auth_sessions.items()
                            if s.get('user', {}).get('username') == username]
        for t in tokens_to_remove:
            _auth_sessions.pop(t, None)
    # 清理用户数据：会话/消息/通知 + 各角色下的记忆目录
    try:
        from app.database import delete_conversation_data
        cid = (target or {}).get('conversation_id') or f"user_{username}"
        delete_conversation_data(cid)
        import shutil
        avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
        if os.path.isdir(avatar_root):
            # 记忆目录名是 mem_key（通常为 username），兼容旧版用 conversation_id 命名的目录
            for persona in os.listdir(avatar_root):
                for mem_key in {username, cid}:
                    mem_dir = os.path.join(avatar_root, persona, "memory", mem_key)
                    if os.path.isdir(mem_dir):
                        shutil.rmtree(mem_dir, ignore_errors=True)
                        logger.info(f"已清理用户记忆: {mem_dir}")
    except Exception as e:
        logger.warning(f"删除用户 {username} 数据清理失败: {e}")
    return {"success": True}


@router.post("/users/update")
async def api_admin_users_update(req: UpdateUserReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    from data.config import hash_password as _hpw
    data = _get_cfg().get_raw()
    users = data.get('web', {}).get('users') or []
    found = None
    for item in users:
        if item.get("username") == req.username:
            found = item
            break
    if not found:
        return JSONResponse(status_code=404, content={"error": "操作失败"})
    if req.password:
        found["password"] = _hpw(req.password)
        # 改密后清除该用户所有 session，强制重新登录（与 reset-password 行为一致）
        with _auth_lock:
            tokens_to_remove = [t for t, s in _auth_sessions.items()
                                if s.get('user', {}).get('username') == req.username]
            for t in tokens_to_remove:
                _auth_sessions.pop(t, None)
    if req.persona:
        # 角色名会成为记忆目录路径组件（avatars/{persona}/memory/...），
        # 这里与 set-persona 保持一致做白名单校验（纵深防御，不依赖下游兜底）。
        persona = safe_persona_name(req.persona)
        if not persona:
            return JSONResponse(status_code=400, content={"error": "无效的角色名"})
        found["persona"] = persona
    if req.persona_prompt is not None:
        found["persona_prompt"] = req.persona_prompt
    if req.persona_override is not None:
        found["persona_override"] = req.persona_override
    if req.library_access is not None:
        found["library_access"] = req.library_access
    # allowed_personas 必须按「是否显式传参」判断：前端用 null 表示恢复“全部允许”。
    # 若沿用 `is not None`，一旦把用户限制成某几个角色，就再也改不回全部允许。
    explicit = getattr(req, "model_fields_set", None)
    if explicit is None:
        explicit = getattr(req, "__fields_set__", set())
    if "allowed_personas" in explicit:
        found["allowed_personas"] = req.allowed_personas
    if req.api_key is not None or req.base_url is not None or req.model is not None:
        api_config = found.setdefault("api_config", {})
        if req.api_key is not None:
            api_config["api_key"] = req.api_key
        if req.base_url is not None:
            api_config["base_url"] = req.base_url
        if req.model is not None:
            api_config["model"] = req.model
    _get_cfg().update('web.users', users)
    return {"success": True}


@router.post("/users/reset-password")
async def api_admin_users_reset_password(req: ResetPasswordReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    from data.config import hash_password as _hpw
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == req.username:
            item['password'] = _hpw(req.new_password)
            _get_cfg().update('web.users', data['web']['users'])
            # 清除该用户的所有 session，强制重新登录
            with _auth_lock:
                tokens_to_remove = [t for t, s in _auth_sessions.items()
                                    if s.get('user', {}).get('username') == req.username]
                for t in tokens_to_remove:
                    _auth_sessions.pop(t, None)
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.post("/users/set-persona")
async def api_admin_users_set_persona(req: SetPersonaReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    # 角色名会被拼进文件系统路径，必须先做白名单校验（与其它端点保持一致）
    persona = safe_persona_name(req.persona)
    if not persona:
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    data = _get_cfg().get_raw()
    # 验证角色是否存在
    avatar_check = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", persona, "avatar.md")
    if not os.path.isfile(avatar_check):
        # 检查用户自创角色
        user_found = False
        for item in data.get('web', {}).get('users') or []:
            if item.get('username') == req.username:
                user_found = True
                user_personas = item.get('user_personas') or {}
                if persona not in user_personas:
                    return JSONResponse(status_code=404, content={"error": f"角色 {persona} 不存在"})
                break
        if not user_found:
            return JSONResponse(status_code=404, content={"error": "用户不存在"})
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == req.username:
            item['persona'] = persona
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.get("/users/{username}/messages")
async def api_admin_user_messages(username: str, request: Request, limit: int = Query(default=100), offset: int = Query(default=0), user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    cid = None
    found = False
    for item in _get_cfg().get_raw().get('web', {}).get('users') or []:
        if item.get('username') == username:
            cid = item.get('conversation_id') or f"user_{username}"
            found = True
            break
    if not found:
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    from app.database import get_history
    msgs, has_more, next_offset = get_history(cid, limit=limit, offset=offset)
    return {"username": username, "conversation_id": cid, "messages": msgs, "has_more": has_more, "next_offset": next_offset}


@router.get("/users/{username}/memory")
async def api_admin_user_memory(username: str, limit: int = Query(default=20), user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    from app.core.deps import get_chat_service
    _chat = get_chat_service()
    persona = None
    found = False
    for item in _get_cfg().get_raw().get('web', {}).get('users') or []:
        if item.get('username') == username:
            persona = item.get('persona') or 'MONO'
            found = True
            break
    if not found:
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    mem = []
    if _chat:
        mem = _chat.memory.get_recent_context(persona, username)[-limit*2:]
    return {"username": username, "persona": persona, "memory": mem}


@router.get("/users/{username}/persona/{name}")
async def api_admin_user_persona(username: str, name: str, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        base = f.read()
    override = ""
    for item in _get_cfg().get_raw().get('web', {}).get('users') or []:
        if item.get('username') == username:
            override = item.get('persona_override', {}).get(name, '')
            break
    return json_utf8_response({"username": username, "name": name, "base_content": base, "override_content": override})


@router.post("/users/{username}/persona/{name}")
async def api_admin_user_persona_save(username: str, name: str, req: SaveUserPersonaContentReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    # 与其他角色端点保持一致：角色名先做白名单校验（这里是 dict key，虽无穿越风险，
    # 但避免把非法名字写进配置导致后续读路径解析失败）
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == username:
            item.setdefault('persona_override', {})[name] = req.content
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.get("/users/{username}/api-config")
async def api_admin_user_api_config(username: str, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == username:
            api_config = item.get('api_config') or {}
            api_key = api_config.get('api_key', '')
            return {
                "username": username,
                "api_key_masked": mask_key(api_key),
                "has_api_key": bool(api_key),
                "base_url": api_config.get('base_url', ''),
                "model": api_config.get('model', ''),
                "has_custom_api": bool(api_key)
            }
    return JSONResponse(status_code=404, content={"error": "用户不存在"})


@router.get("/users/api-configs")
async def api_admin_users_api_configs(user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    result = []
    for item in data.get('web', {}).get('users') or []:
        api_config = item.get('api_config') or {}
        result.append({
            "username": item.get('username'),
            "has_custom_api": bool(api_config.get('api_key')),
            "base_url": api_config.get('base_url', ''),
            "model": api_config.get('model', '')
        })
    return {"users": result}


# ===== 性格库 =====

@router.post("/persona-override")
async def api_admin_persona_override(req: PersonaOverrideReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    admin = data.get('web', {}).get('admin') or {}
    overrides = admin.get('admin_persona_override') or {}
    overrides[req.persona] = req.content
    admin['admin_persona_override'] = overrides
    data['web']['admin'] = admin
    _get_cfg().update('web.admin', admin)
    return {"success": True}


@router.get("/persona-library")
async def api_admin_persona_library(user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    library = _get_cfg().get_raw().get('web', {}).get('persona_library') or []
    return json_utf8_response({"library": library})


@router.post("/persona-library/save")
async def api_admin_persona_library_save(req: PersonaLibraryReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    library = data.get('web', {}).get('persona_library') or []
    found = False
    for item in library:
        if item.get("name") == req.name:
            item.update({
                "name": req.name,
                "content": req.content,
                "persona_name": req.persona_name,
                "public": req.public,
            })
            found = True
            break
    if not found:
        library.append({
            "name": req.name,
            "content": req.content,
            "persona_name": req.persona_name,
            "public": req.public,
        })
    _get_cfg().update('web.persona_library', library)
    return {"success": True}


@router.post("/persona-library/delete")
async def api_admin_persona_library_delete(req: DeleteLibraryReq, user: dict = Depends(require_admin)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    library = data.get('web', {}).get('persona_library', [])
    filtered = [x for x in library if x.get("name") != req.name]
    _get_cfg().update('web.persona_library', filtered)
    return {"success": True}
