"""
认证路由 — 登录/登出/当前用户/改密/验证密码/查看 key
从 server.py 拆出，使用 Depends 依赖注入
"""
import time, secrets, logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.deps import get_current_user, _auth_sessions, _get_token_from_request, _auth_lock
from app.core.responses import APIError

logger = logging.getLogger("motochat")
router = APIRouter()


class LoginReq(BaseModel):
    username: str
    password: str

class VerifyPasswordReq(BaseModel):
    password: str

class RevealKeyReq(BaseModel):
    password: str
    key_type: str = "llm"

class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str


# 登录限流（仅记录失败尝试）
_LOGIN_ATTEMPTS = {}


def _check_login_rate(ip: str) -> bool:
    """检查IP是否超过登录限流。返回True表示允许，False表示被限流。"""
    now = time.time()
    attempts = _LOGIN_ATTEMPTS.get(ip, [])
    attempts = [t for t in attempts if now - t < 60]
    if len(attempts) >= 10:
        return False
    # 不在这里追加，只在登录失败时由调用方追加
    _LOGIN_ATTEMPTS[ip] = attempts
    # 定期清理过期条目，防止字典无限增长
    if len(_LOGIN_ATTEMPTS) > 1000:
        expired_ips = [k for k, times in _LOGIN_ATTEMPTS.items() if not any(now - t < 60 for t in times)]
        for k in expired_ips:
            del _LOGIN_ATTEMPTS[k]
    return True


def _record_login_failure(ip: str):
    """记录一次登录失败"""
    now = time.time()
    attempts = _LOGIN_ATTEMPTS.get(ip, [])
    attempts = [t for t in attempts if now - t < 60]
    attempts.append(now)
    _LOGIN_ATTEMPTS[ip] = attempts


def _clear_login_attempts(ip: str):
    """登录成功后清除该IP的失败记录"""
    _LOGIN_ATTEMPTS.pop(ip, None)


def _find_user(username: str):
    """查找用户配置（复用 core/utils.find_user_config）"""
    from app.core.utils import find_user_config
    return find_user_config(username)


@router.post("/login")
async def api_auth_login(request: Request, req: LoginReq):
    ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(ip):
        return JSONResponse(status_code=429, content={"success": False, "error": "请求过于频繁，请稍后再试"})
    # 清理过期 session 防止内存泄漏（加锁避免并发修改）
    now_ts = time.time()
    with _auth_lock:
        expired_tokens = [t for t, s in _auth_sessions.items() if now_ts > s.get('expires_at', 0)]
        for t in expired_tokens:
            _auth_sessions.pop(t, None)
    from data.config import verify_password as _vfy
    user = _find_user(req.username)
    if not user or not user.get('password') or not _vfy(req.password, user['password']):
        # 登录失败时记录，成功时不占用额度
        _record_login_failure(ip)
        return JSONResponse(status_code=401, content={"success": False, "error": "账号或密码错误"})
    # 登录成功，清除该IP的失败记录
    _clear_login_attempts(ip)
    token = secrets.token_hex(24)
    with _auth_lock:
        _auth_sessions[token] = {
            "user": {"username": user['username'], "role": user['role'],
                     "persona": user.get('persona'), "conversation_id": user.get('conversation_id')},
            "created_at": time.time(),
            "expires_at": time.time() + 60 * 60 * 24 * 7
        }
    resp_data = {"success": True, "token": token, "username": user['username'],
                 "role": user['role'], "persona": user.get('persona'),
                 "conversation_id": user.get('conversation_id')}
    resp = JSONResponse(content=resp_data)
    # 注意: 不设置 secure=True，因为本地开发环境使用 HTTP
    # 生产环境部署时应添加 secure=True
    resp.set_cookie("kc_token", token, max_age=7 * 24 * 3600, httponly=True, samesite="lax", path="/")
    return resp


@router.post("/logout")
async def api_auth_logout(request: Request):
    token = _get_token_from_request(request)
    if token:
        with _auth_lock:
            _auth_sessions.pop(token, None)
    resp = JSONResponse(content={"success": True})
    resp.delete_cookie("kc_token", path="/")
    return resp


@router.get("/me")
async def api_auth_me(user: dict = Depends(get_current_user)):
    if not user:
        raise APIError(401, "未登录")
    return user


@router.post("/verify-password")
async def api_auth_verify_password(request: Request, req: VerifyPasswordReq, user: dict = Depends(get_current_user)):
    if not user or user.get("role") != "admin":
        raise APIError(403, "需要管理员权限")
    # 复用登录同款限流，防止无限爆破 admin 密码
    ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(ip):
        return JSONResponse(status_code=429, content={"success": False, "error": "请求过于频繁，请稍后再试"})
    from data.config import get_config as _get_cfg
    from data.config import verify_password as _vfy
    stored_pw = _get_cfg().web.admin.password
    if not stored_pw:
        raise APIError(403, "管理员密码未设置")
    if _vfy(req.password, stored_pw):
        return {"authenticated": True, "success": True}
    _record_login_failure(ip)
    raise APIError(401, "旧密码不正确")


@router.post("/reveal-key")
async def api_auth_reveal_key(request: Request, req: RevealKeyReq, user: dict = Depends(get_current_user)):
    if not user or user.get("role") != "admin":
        raise APIError(403, "需要管理员权限")
    # 复用登录同款限流，防止无限爆破 admin 密码
    ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(ip):
        return JSONResponse(status_code=429, content={"success": False, "error": "请求过于频繁，请稍后再试"})
    from data.config import get_config as _get_cfg
    from data.config import verify_password as _vfy
    stored_pw = _get_cfg().web.admin.password
    if not stored_pw:
        raise APIError(403, "管理员密码未设置")
    if not _vfy(req.password, stored_pw):
        _record_login_failure(ip)
        raise APIError(401, "旧密码不正确")
    key = ""
    if req.key_type == "vision":
        key = _get_cfg().vision.get('api_key', '') or ''
    elif req.key_type == "tts":
        key = _get_cfg().tts.get('api_key', '') or ''
    else:
        key = _get_cfg().llm.get('api_key', '') or ''
    return {"success": True, "key": key}


@router.post("/change-password")
async def api_auth_change_password(request: Request, req: ChangePasswordReq, user: dict = Depends(get_current_user)):
    if not user:
        raise APIError(401, "未登录")
    from data.config import get_config as _get_cfg
    from data.config import verify_password as _vfy, hash_password as _hpw
    if not req.new_password or len(req.new_password) < 4:
        raise APIError(400, "新密码至少 4 位")
    if user.get("role") == "admin":
        stored_pw = _get_cfg().web.admin.password
        if not stored_pw:
            raise APIError(403, "管理员密码未初始化")
        if not _vfy(req.old_password, stored_pw):
            raise APIError(401, "旧密码不正确")
        _get_cfg().update("web.admin.password", _hpw(req.new_password))
        return {"success": True}
    data = _get_cfg().get_raw()
    users = data.get("web", {}).get("users") or []
    username = user.get("username")
    updated = False
    for item in users:
        if item.get("username") == username:
            if not _vfy(req.old_password, item.get("password", "")):
                raise APIError(401, "旧密码不正确")
            item["password"] = _hpw(req.new_password)
            updated = True
            break
    if not updated:
        raise APIError(404, "用户不存在")
    _get_cfg().update("web.users", users)
    return {"success": True}
