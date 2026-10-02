"""
用户角色路由 — 用户自定义角色管理
从 server.py 拆分，使用 Depends 依赖注入
"""
import os
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.deps import require_user
from app.core.utils import ROOT, safe_persona_name, json_utf8_response

logger = logging.getLogger("motochat")
router = APIRouter()


# ===== 请求模型 =====

class CreateUserPersonaReq(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    content: str = "# 角色\n\n你是一个友好的AI助手。"

class RenameUserPersonaReq(BaseModel):
    old_name: str
    new_name: str = Field(min_length=1, max_length=32)

class SelectUserPersonaReq(BaseModel):
    name: str

class SaveUserPersonaReq(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    content: str = Field(max_length=50000)

class ResetUserPersonaReq(BaseModel):
    name: str

class ApplyLibraryReq(BaseModel):
    library_name: str
    persona: str

class ApproveOverrideReq(BaseModel):
    persona: str
    approve: bool = True


# ===== 用户角色管理 =====

@router.get("/user/personas")
async def api_user_personas(request: Request, user: dict = Depends(require_user)):
    from app.core.deps import get_chat_service
    from data.config import get_config as _get_cfg
    _chat = get_chat_service()
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    current_persona = None
    allowed = None
    if user.get("role") == "user":
        for item in _get_cfg().get_raw().get("web", {}).get("users") or []:
            if item.get("username") == user["username"]:
                current_persona = item.get("persona") or None
                allowed = item.get("allowed_personas")
                break
    result = []
    # 目录不存在时返回空列表而非 500
    try:
        entries = sorted(os.listdir(avatar_root))
    except OSError:
        entries = []
    for name in entries:
        p2 = os.path.join(avatar_root, name, "avatar.md")
        if os.path.isfile(p2) and (allowed is None or name in allowed):
            result.append({"name": name, "active": name == (current_persona or (_chat.avatar_name if _chat else None))})
    # Add user-created personas
    for item in _get_cfg().get_raw().get("web", {}).get("users") or []:
        if item.get("username") == user["username"]:
            for up_name in item.get("user_personas") or {}:
                result.append({"name": up_name, "active": up_name == current_persona, "user_created": True})
            break
    return {"personas": result, "current": current_persona or (_chat.avatar_name if _chat else None)}


@router.get("/user/persona/{name}/preview")
async def api_user_persona_preview(name: str, request: Request, user: dict = Depends(require_user)):
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    from app.core.deps import get_chat_service
    _chat = get_chat_service()
    mem = []
    if _chat and user.get("role") == "user":
        mem = _chat.memory.get_recent_context(name, user['username'])[-10:]
    return {"name": name, "memory": mem}


@router.post("/user/persona/create")
async def api_user_persona_create(req: CreateUserPersonaReq, user: dict = Depends(require_user)):
    name = safe_persona_name(req.name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "请输入角色名称"})
    # Check global personas for duplicate
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    if os.path.isdir(os.path.join(avatar_root, name)):
        return JSONResponse(status_code=400, content={"error": f"角色 {name} 已存在（系统角色）"})
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            user_personas = item.setdefault('user_personas', {})
            if name in user_personas:
                return JSONResponse(status_code=400, content={"error": f"角色 {name} 已存在"})
            user_personas[name] = req.content
            # Also add to allowed_personas if not already
            allowed = item.get('allowed_personas')
            if allowed is not None and name not in allowed:
                allowed.append(name)
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.post("/user/persona/rename")
async def api_user_persona_rename(req: RenameUserPersonaReq, user: dict = Depends(require_user)):
    # 新旧名称都需校验：两者都会作为 user_personas 的 key 落进配置并被后续路径解析使用
    old_name = safe_persona_name(req.old_name or "")
    new_name = safe_persona_name(req.new_name)
    if not old_name or not new_name:
        return JSONResponse(status_code=400, content={"error": "请提供合法的新旧名称"})
    if old_name == new_name:
        return JSONResponse(status_code=400, content={"error": "新旧名称相同"})
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            user_personas = item.get('user_personas') or {}
            if old_name not in user_personas:
                return JSONResponse(status_code=404, content={"error": "角色不存在"})
            if new_name in user_personas:
                return JSONResponse(status_code=400, content={"error": "名称已被使用"})
            # Move content
            user_personas[new_name] = user_personas.pop(old_name)
            # Update persona_override
            overrides = item.get('persona_override') or {}
            if old_name in overrides:
                overrides[new_name] = overrides.pop(old_name)
            # Update pending
            pending = item.get('pending_persona_override') or {}
            if old_name in pending:
                pending[new_name] = pending.pop(old_name)
            # Update allowed_personas
            allowed = item.get('allowed_personas')
            if allowed and old_name in allowed:
                allowed[allowed.index(old_name)] = new_name
            # Update current persona if selected
            if item.get('persona') == old_name:
                item['persona'] = new_name
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.get("/user/persona/{name}")
async def api_user_persona_detail(name: str, request: Request, user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    user_override = ""
    persona_prompt_val = ""
    pending_override = ""
    if user.get("role") == "user":
        for item in _get_cfg().get_raw().get('web', {}).get('users') or []:
            if item.get('username') == user['username']:
                user_personas = item.get('user_personas') or {}
                if name in user_personas:
                    user_override = item.get('persona_override', {}).get(name, '')
                    persona_prompt_val = item.get('persona_prompt', '')
                    pending_override = item.get('pending_persona_override', {}).get(name, '')
                    return json_utf8_response({
                        "name": name,
                        "base_content": user_personas[name],
                        "user_content": user_override,
                        "persona_prompt": persona_prompt_val,
                        "pending_content": pending_override,
                        "user_created": True
                    })
                user_override = item.get('persona_override', {}).get(name, '')
                persona_prompt_val = item.get('persona_prompt', '')
                pending_override = item.get('pending_persona_override', {}).get(name, '')
                break
    # 路径穿越防护：访问文件系统前必须校验角色名（与其他端点一致）
    if not safe_persona_name(name):
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        base = f.read()
    return json_utf8_response({
        "name": name,
        "base_content": base,
        "user_content": user_override,
        "persona_prompt": persona_prompt_val,
        "pending_content": pending_override
    })


@router.post("/user/persona/reset")
async def api_user_persona_reset(req: ResetUserPersonaReq, user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            changed = False
            override = item.get('persona_override') or {}
            if req.name in override:
                del override[req.name]
                item['persona_override'] = override
                changed = True
            if item.get('persona_prompt'):
                item['persona_prompt'] = ''
                changed = True
            if changed:
                _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.post("/user/persona/select")
async def api_user_persona_select(req: SelectUserPersonaReq, user: dict = Depends(require_user)):
    name = safe_persona_name(req.name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    # Check if global or user-created persona exists
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    is_user_persona = False
    if not os.path.isfile(avatar_path):
        # Check user-created personas
        for item in data.get('web', {}).get('users') or []:
            if item.get('username') == user['username']:
                if name in (item.get('user_personas') or {}):
                    is_user_persona = True
                break
        if not is_user_persona:
            return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            allowed = item.get('allowed_personas')
            if allowed is not None and name not in allowed and not is_user_persona:
                return JSONResponse(status_code=403, content={"error": "你无权访问此角色"})
            item['persona'] = name
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.post("/user/persona/save")
async def api_user_persona_save(req: SaveUserPersonaReq, user: dict = Depends(require_user)):
    name = safe_persona_name(req.name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "无效的角色名"})
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            # Check allowed_personas
            allowed = item.get('allowed_personas')
            if allowed is not None and name not in allowed:
                return JSONResponse(status_code=403, content={"error": "你无权访问此角色"})
            item.setdefault('persona_override', {})[name] = req.content
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.get("/user/pending-overrides")
async def api_user_pending_overrides(user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    pending = {}
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            pending = item.get('pending_persona_override') or {}
            break
    return {"pending": pending}


@router.post("/user/approve-override")
async def api_user_approve_override(req: ApproveOverrideReq, user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            pending = item.get('pending_persona_override') or {}
            if req.persona not in pending:
                return JSONResponse(status_code=404, content={"error": "无待审批内容"})
            if req.approve:
                item.setdefault('persona_override', {})[req.persona] = pending[req.persona]
            del pending[req.persona]
            item['pending_persona_override'] = pending
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True, "action": "approved" if req.approve else "rejected"}
    return JSONResponse(status_code=404, content={"error": "操作失败"})


@router.get("/user/persona-library")
async def api_user_persona_library(user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    library = _get_cfg().get_raw().get('web', {}).get('persona_library') or []
    library_access = False
    for item in _get_cfg().get_raw().get('web', {}).get('users') or []:
        if item.get("username") == user["username"]:
            library_access = item.get("library_access", False)
            break
    if not library_access:
        return json_utf8_response({"library": [], "access_denied": True})
    public_items = [x for x in library if x.get("public", True)]
    return json_utf8_response({"library": public_items})


@router.post("/user/persona/apply-library")
async def api_user_persona_apply_library(req: ApplyLibraryReq, user: dict = Depends(require_user)):
    from data.config import get_config as _get_cfg
    data = _get_cfg().get_raw()
    library = data.get('web', {}).get('persona_library') or []
    lib_item = None
    for x in library:
        if x.get("name") == req.library_name and x.get("public", True):
            lib_item = x
            break
    if not lib_item:
        return JSONResponse(status_code=404, content={"error": "模板不存在"})
    for item in data.get('web', {}).get('users') or []:
        if item.get("username") == user["username"]:
            item.setdefault('persona_override', {})[req.persona] = lib_item.get("content", "")
            _get_cfg().update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "操作失败"})
