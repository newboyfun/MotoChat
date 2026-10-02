"""
依赖注入 — 消除 server.py 里的全局 _chat/_tts/_reminder/_autosend 状态
端点用 Depends(get_current_user) / Depends(get_chat_service) 拿依赖
"""
import time, logging, threading
from typing import Optional

from fastapi import Request

logger = logging.getLogger("motochat")

# 服务容器（由 server.inject() 设置）
_services = {
    "chat": None,
    "tts": None,
    "reminder": None,
    "autosend": None,
}

def set_service(name: str, service):
    _services[name] = service

def get_chat_service():
    return _services["chat"]

def get_tts_service():
    return _services["tts"]

def get_reminder_service():
    return _services["reminder"]

def get_autosend_service():
    return _services["autosend"]


# ===== 认证依赖 =====

# session 存储（由 server.py 共享，避免重复定义）
# 注意: 使用 threading.Lock 而非 asyncio.Lock，因为部分同步端点也需要访问
# 锁持有时间极短（仅 dict 读写），不会实际阻塞事件循环
_auth_sessions: dict = {}
_auth_lock = threading.Lock()
_MAX_SESSIONS = 1000  # session 上限，防止内存无限增长
_last_cleanup = 0  # 上次清理时间戳
_CLEANUP_INTERVAL = 300  # 每 5 分钟最多清理一次

def _get_token_from_request(request: Request) -> str:
    # 只接受 Authorization header / HttpOnly Cookie。
    # 不支持 ?token= query：uvicorn access_log 会把带 token 的 URL 落盘，造成凭据泄露。
    auth = request.headers.get('authorization') or ''
    if auth.lower().startswith('bearer '):
        return auth.split(' ', 1)[1].strip()
    return (request.cookies.get('kc_token') or '').strip()

def get_current_user(request: Request) -> Optional[dict]:
    """FastAPI 依赖：返回当前登录用户 dict，未登录返回 None。
    端点里用 user = Depends(get_current_user) 即可。"""
    global _last_cleanup
    token = _get_token_from_request(request)
    if not token:
        return None
    now = time.time()
    with _auth_lock:
        # 定期清理过期 session，防止内存无限增长
        if now - _last_cleanup > _CLEANUP_INTERVAL or len(_auth_sessions) > _MAX_SESSIONS:
            expired = [t for t, s in _auth_sessions.items() if now > s.get('expires_at', 0)]
            for t in expired:
                _auth_sessions.pop(t, None)
            # 仅清过期项不足以收敛规模：若仍有大量“未过期”会话（如被反复登录刷出），
            # 按创建时间淘汰最旧的若干条，保证字典大小有硬上限。
            overflow = len(_auth_sessions) - _MAX_SESSIONS
            if overflow > 0:
                oldest = sorted(_auth_sessions.items(),
                                key=lambda kv: kv[1].get('created_at', 0))[:overflow]
                for t, _ in oldest:
                    _auth_sessions.pop(t, None)
                logger.debug(f"session 超限，按创建时间淘汰 {len(oldest)} 条")
            _last_cleanup = now
        session = _auth_sessions.get(token)
        if not session:
            return None
        if now > session.get('expires_at', 0):
            _auth_sessions.pop(token, None)
            return None
        return session.get('user')

def require_user(request: Request) -> dict:
    """FastAPI 依赖：要求登录，否则抛 401"""
    from app.core.responses import APIError
    user = get_current_user(request)
    if not user:
        raise APIError(401, "请先登录")
    return user

def require_admin(request: Request) -> dict:
    """FastAPI 依赖：要求管理员登录，否则抛 403"""
    from app.core.responses import APIError
    user = get_current_user(request)
    if not user:
        raise APIError(401, "请先登录")
    if user.get("role") != "admin":
        raise APIError(403, "需要管理员权限")
    return user
