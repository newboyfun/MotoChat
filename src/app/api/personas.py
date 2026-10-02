"""
角色管理路由 — 角色 CRUD、切换、提示词管理、头像管理
从 server.py 拆分，使用 Depends 依赖注入
"""
import os
import time
import logging
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.deps import require_admin, get_current_user
from app.core.utils import ROOT, safe_persona_name, json_utf8_response, sniff_image_ext

logger = logging.getLogger("motochat")
router = APIRouter()

# 内置静态头像映射（无用户上传时的回退）
_BUILTIN_AVATARS = {"MONO": "avatar-mono.jpg", "SAGE": "avatar-sage.jpg"}
_DEFAULT_AVATAR = "avatar-default.jpg"


# ===== 请求模型 =====

class CreatePersonaReq(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    content: str = "# 角色\n\n你是一个友好的AI助手。"

class UpdatePersonaReq(BaseModel):
    content: str

class SwitchPersonaReq(BaseModel):
    name: str


# ===== 角色管理 =====

@router.get("")
async def api_personas(request: Request, user: dict = Depends(get_current_user)):
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    from app.core.deps import get_chat_service
    _chat = get_chat_service()
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    active = _chat.avatar_name if _chat else None
    result = []
    # 目录不存在时返回空列表而非 500；过滤掉非目录条目
    try:
        entries = sorted(os.listdir(avatar_root))
    except OSError:
        entries = []
    for name in entries:
        if not os.path.isdir(os.path.join(avatar_root, name)):
            continue
        p = os.path.join(avatar_root, name, "avatar.md")
        result.append({
            "name": name,
            "path": f"data/avatars/{name}",
            "has_avatar": os.path.isfile(p),
            "active": name == active,
        })
    return json_utf8_response({"personas": result})


@router.get("/{name}")
async def api_persona_detail(name: str, request: Request, user: dict = Depends(get_current_user)):
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        content = f.read()
    return json_utf8_response({"name": name, "content": content})


@router.get("/{name}/prompt")
async def api_persona_prompt(name: str, request: Request, user: dict = Depends(get_current_user)):
    if not user:
        return JSONResponse(status_code=401, content={"error": "请先登录"})
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        return json_utf8_response({"name": name, "content": f.read().strip()})


@router.post("/create")
async def api_create_persona(req: CreatePersonaReq, user: dict = Depends(require_admin)):
    name = safe_persona_name(req.name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    if os.path.isdir(avatar_path):
        return JSONResponse(status_code=400, content={"error": f"角色 {name} 已存在"})
    try:
        os.makedirs(avatar_path, exist_ok=True)
        with open(os.path.join(avatar_path, "avatar.md"), "w", encoding="utf-8") as f:
            f.write(req.content)
        logger.info(f"角色创建成功: {name}")
        return {"success": True}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})


@router.post("/{name}/update")
async def api_update_persona(name: str, req: UpdatePersonaReq, user: dict = Depends(require_admin)):
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    try:
        with open(avatar_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        from app.core.deps import get_chat_service
        _chat = get_chat_service()
        if _chat and _chat.avatar_name == name:
            _chat.update_avatar_prompt(req.content, avatar_path)
        logger.info(f"[角色] 设定已更新: {name}")
        return {"success": True}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})


@router.post("/{name}/delete")
async def api_delete_persona(name: str, user: dict = Depends(require_admin)):
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    if not os.path.isdir(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    from app.core.deps import get_chat_service
    _chat = get_chat_service()
    if _chat and _chat.avatar_name == name:
        return JSONResponse(status_code=400, content={"error": "不能删除当前正在使用的角色"})
    try:
        import shutil
        shutil.rmtree(avatar_path)
        logger.info(f"[角色] 已删除: {name}")
        return {"success": True}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})


@router.post("/{name}/prompt/update")
async def api_persona_update_prompt(name: str, req: UpdatePersonaReq, user: dict = Depends(require_admin)):
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    try:
        with open(avatar_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        from app.core.deps import get_chat_service
        _chat = get_chat_service()
        if _chat and _chat.avatar_name == name:
            _chat.update_avatar_prompt(req.content, avatar_path)
        return {"success": True}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})


@router.post("/switch")
async def api_persona_switch(req: SwitchPersonaReq, user: dict = Depends(require_admin)):
    name = safe_persona_name(req.name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    full = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    avatar_path = os.path.join(full, "avatar.md")
    if not os.path.isdir(full) or not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    try:
        from app.core.deps import get_chat_service
        _chat = get_chat_service()
        if _chat:
            # switch_avatar 内部已通过锁更新 avatar_name、avatar_prompt、avatar_names
            _chat.switch_avatar(full)
        from data.config import get_config as _get_cfg
        _get_cfg().update('behavior.avatar_dir', full)
        return {"success": True}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})


# ===== 角色头像 =====

_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5MB


def _persona_avatar_path(name: str) -> str:
    return os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.jpg")


@router.get("/{name}/avatar")
async def api_persona_avatar(name: str):
    """获取角色头像：用户上传的优先，其次内置专属头像，最后默认头像（重定向到静态资源）"""
    name = safe_persona_name(name)
    if name:
        p = _persona_avatar_path(name)
        if os.path.isfile(p):
            # no-store：上传新头像后前端能立即看到更新
            return FileResponse(p, media_type="image/jpeg",
                                headers={"Cache-Control": "no-store"})
    builtin = _BUILTIN_AVATARS.get((name or "").upper(), _DEFAULT_AVATAR)
    return RedirectResponse(url=f"/static/assets/{builtin}", status_code=302)


@router.post("/{name}/avatar")
async def api_persona_avatar_upload(name: str, file: UploadFile = File(...), user: dict = Depends(require_admin)):
    """上传角色头像：校验 → Pillow 重编码为 512x512 JPEG → 存 userdata/avatars/{name}/avatar.jpg"""
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    persona_dir = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    if not os.path.isdir(persona_dir):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    try:
        data = await file.read()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "文件读取失败"})
    if not data or len(data) > _AVATAR_MAX_BYTES:
        return JSONResponse(status_code=400, content={"error": "图片大小需在 5MB 以内"})
    if not sniff_image_ext(data):
        return JSONResponse(status_code=400, content={"error": "仅支持 JPG / PNG / WebP / GIF 图片"})
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data)).convert("RGB")
        # 中心裁方 + 缩放到 512，头像展示全部为圆形裁切
        w, h = im.size
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        if side > 512:
            im = im.resize((512, 512), Image.LANCZOS)
        out = _persona_avatar_path(name)
        im.save(out, "JPEG", quality=88, optimize=True)
        logger.info(f"[角色] 头像已更新: {name} ({os.path.getsize(out)//1024}KB)")
        return {"success": True, "version": int(time.time())}
    except Exception as e:
        logger.error(f"头像处理失败: {e}", exc_info=True)
        return JSONResponse(status_code=400, content={"error": "图片处理失败，请换一张试试"})


@router.delete("/{name}/avatar")
async def api_persona_avatar_delete(name: str, user: dict = Depends(require_admin)):
    """重置角色头像为默认"""
    name = safe_persona_name(name)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不合法"})
    p = _persona_avatar_path(name)
    if os.path.isfile(p):
        try:
            os.remove(p)
            logger.info(f"[角色] 头像已重置: {name}")
        except OSError as e:
            logger.error(f"头像删除失败: {e}")
            return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})
    return {"success": True}
