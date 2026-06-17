"""
MotoChat FastAPI 服务器 — REST API + WebSocket
增强版：详细日志 + 完整 API
"""
import os, json, asyncio, logging, sys, secrets, time
from typing import List
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Query
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse, Response


def json_utf8_response(data):
    return JSONResponse(content=data, headers={'Content-Type': 'application/json; charset=utf-8'})

from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# ===== Auth =====

# 简易登录限流
_LOGIN_ATTEMPTS = {}

def _check_login_rate(ip: str) -> bool:
    now = time.time()
    attempts = _LOGIN_ATTEMPTS.get(ip, [])
    attempts = [t for t in attempts if now - t < 60]
    _LOGIN_ATTEMPTS[ip] = attempts
    if len(attempts) >= 10:
        return False
    attempts.append(now)
    _LOGIN_ATTEMPTS[ip] = attempts
    return True

_auth_sessions = {}

def _get_token_from_request(request):
    auth = request.headers.get('authorization') or ''
    if auth.lower().startswith('bearer '):
        return auth.split(' ', 1)[1].strip()
    cookie_token = request.cookies.get('kc_token') or ''
    if cookie_token:
        return cookie_token
    try:
        return request.query_params.get('token') or ''
    except Exception:
        return ''

def _current_user(request):
    from data.config import config as _cfg
    token = _get_token_from_request(request)
    if not token:
        return None
    session = _auth_sessions.get(token)
    if not session:
        return None
    if time.time() > session.get('expires_at', 0):
        _auth_sessions.pop(token, None)
        return None
    return session.get('user')

def _find_user(username: str):
    from data.config import config as _cfg
    username = (username or '').strip()
    data = _cfg.get_raw()
    web = data.get('web', {})
    admin = web.get('admin') or {}
    if admin.get('username') and admin.get('username') == username:
        return {'username': admin['username'], 'password': admin.get('password', ''), 'role': 'admin', 'persona': None, 'conversation_id': None, 'admin_persona_override': admin.get('admin_persona_override', {}), 'library_access': admin.get('library_access', True)}
    for item in web.get('users') or []:
        if item.get('username') == username:
            return {'username': item['username'], 'password': item.get('password', ''), 'role': item.get('role', 'user'), 'persona': item.get('persona') or 'MONO', 'conversation_id': item.get('conversation_id') or f"user_{item['username']}", 'library_access': item.get('library_access', False)}
    return None

def _get_session_secret():
    from data.config import config as _cfg
    data = _cfg.get_raw().get('web', {})
    secret = data.get('secret') or ''
    if not secret:
        secret = secrets.token_hex(24)
        _cfg.update('web.secret', secret)
    return secret

def _ensure_default_users():
    from data.config import config as _cfg
    data = _cfg.get_raw().get('web', {})
    changed = False
    if not data.get('admin', {}).get('username'):
        _cfg.update('web.admin.username', 'admin')
        changed = True
    if not data.get('admin', {}).get('password'):
        _cfg.update('web.admin.password', secrets.token_hex(6))
        changed = True
    if not data.get('secret'):
        _cfg.update('web.secret', secrets.token_hex(24))
        changed = True
    if not data.get('users'):
        _cfg.update('web.users', [{'username': 'user', 'password': secrets.token_hex(6), 'role': 'user'}])
        changed = True
    return changed

logger = logging.getLogger("motochat")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# ===== 日志配置 =====
class _DualHandler(logging.Handler):
    """双通道(控制台+网页)"""
    def __init__(self):
        super().__init__()
        self._console = logging.StreamHandler(sys.stdout)
        self._console.setFormatter(logging.Formatter(
            "  [%(asctime)s] %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
        self._buffer = []

    def emit(self, record):
        try:
            # 杈撳嚭鍒版帶鍒跺彴(榛戞)
            self._console.emit(record)
            # 存入内存缓冲(网页日志)
            msg = self._console.format(record)
            self._buffer.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname.lower(),
                "message": msg.strip()
            })
            if len(self._buffer) > 2000:
                self._buffer.pop(0)
        except Exception as e:
            logger.debug(f"日志写入失败: {e}")

_log_handler = _DualHandler()
logging.getLogger("motochat").addHandler(_log_handler)
logging.getLogger("motochat").setLevel(logging.DEBUG)
logging.getLogger("uvicorn").addHandler(_log_handler)
logging.getLogger("uvicorn.access").addHandler(_log_handler)
logging.getLogger("uvicorn.error").addHandler(_log_handler)

def add_log(level, msg):
    """记录(等级/消息)"""
    getattr(logger, level, logger.info)(msg)

def get_log_buffer():
    return _log_handler._buffer

# ===== 应用 =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="MotoChat", version="2.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.middleware("http")
async def add_cache_headers(request, call_response):
    response = await call_response(request)
    if request.url.path.startswith("/static/"):
        # Cache static assets for 60 seconds only (avoid stale cache during dev)
        response.headers["Cache-Control"] = "public, max-age=60"
    return response

_chat = None
_tts = None
_reminder = None
_autosend = None

def inject(chat_service, tts_service, reminder_service, autosend_service=None):
    global _chat, _tts, _reminder, _autosend
    _chat = chat_service
    _tts = tts_service
    _reminder = reminder_service
    _autosend = autosend_service

class ChatReq(BaseModel):
    message: str
    conversation_id: str = "default"

class CmdReq(BaseModel):
    command: str
    conversation_id: str = "default"

class TTSReq(BaseModel):
    text: str

class LoginReq(BaseModel):
    username: str
    password: str


_ws_clients: List[WebSocket] = []

async def broadcast(data: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)

# ---------- 首页 ----------
@app.on_event("startup")
async def on_startup():
    try:
        _ensure_default_users()
    except Exception as e:
        logger.debug(f"启动默认用户初始化失败: {e}")
    # Inject running event loop into reminder and autosend services for WebSocket broadcast
    try:
        loop = asyncio.get_running_loop()
        if _reminder and hasattr(_reminder, 'set_event_loop'):
            _reminder.set_event_loop(loop)
        if _autosend and hasattr(_autosend, 'set_event_loop'):
            _autosend.set_event_loop(loop)
        logger.info("事件循环已注入提醒/自动消息服务")
    except Exception as e:
        logger.debug(f"事件循环注入失败: {e}")

@app.post("/api/auth/login")
async def api_auth_login(request: Request, req: LoginReq):
    ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(ip):
        return JSONResponse(status_code=429, content={"success": False, "error": "请求过于频繁，请稍后再试"})
    from data.config import config as _cfg
    user = _find_user(req.username)
    if not user or not user.get('password') or user['password'] != req.password:
        return JSONResponse(status_code=401, content={"success": False, "error": "账号或密码错误"})
    token = secrets.token_hex(24)
    _auth_sessions[token] = {
        "user": {"username": user['username'], "role": user['role'], "persona": user.get('persona'), "conversation_id": user.get('conversation_id')},
        "created_at": time.time(),
        "expires_at": time.time() + 60*60*24*7
    }
    resp_data = {"success": True, "token": token, "username": user['username'], "role": user['role'], "persona": user.get('persona'), "conversation_id": user.get('conversation_id')}
    from starlette.responses import JSONResponse as _JR
    resp = _JR(content=resp_data)
    resp.set_cookie("kc_token", token, max_age=7*24*3600, httponly=True, samesite="lax", path="/")
    return resp

@app.post("/api/auth/logout")
async def api_auth_logout(request: Request):
    token = _get_token_from_request(request)
    if token:
        _auth_sessions.pop(token, None)
    resp = JSONResponse(content={"success": True})
    resp.delete_cookie("kc_token", path="/")
    return resp

@app.post("/api/user/persona")
async def api_user_persona(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户权限"})
    from data.config import config as _cfg
    content = (req.get("content") or "").strip()
    if not content:
        return JSONResponse(status_code=400, content={"error": "内容不能为空"})
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            item['persona_prompt'] = content
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    return user

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    with open(os.path.join(STATIC, "login.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = _current_user(request)
    if not user:
        with open(os.path.join(STATIC, "login.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    if user.get("role") == "admin":
        with open(os.path.join(STATIC, "index.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    with open(os.path.join(STATIC, "user.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=302)
    with open(os.path.join(STATIC, "index.html"), "r", encoding="utf-8") as f:
        resp = HTMLResponse(f.read())
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

@app.get("/user", response_class=HTMLResponse)
async def user_app(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return RedirectResponse(url="/login", status_code=302)
    with open(os.path.join(STATIC, "user.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    with open(os.path.join(STATIC, "admin-users.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(status_code=200, content={})

# ---------- REST API ----------
@app.post("/api/admin/users/reset-password")
async def api_admin_users_reset_password(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    username = (req.get("username") or "").strip()
    new_password = (req.get("new_password") or "").strip()
    if not username or not new_password:
        return JSONResponse(status_code=400, content={"error": "请提供用户名和新密码"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == username:
            item['password'] = new_password
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.post("/api/admin/users/set-persona")
async def api_admin_users_set_persona(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    username = (req.get("username") or "").strip()
    persona = (req.get("persona") or "").strip()
    if not username or not persona:
        return JSONResponse(status_code=400, content={"error": "请提供用户名和角色名"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == username:
            item['persona'] = persona
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.get("/api/admin/users/{username}/messages")
async def api_admin_user_messages(username: str, request: Request, limit: int = Query(default=100), offset: int = Query(default=0)):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    from data.config import config as _cfg
    cid = None
    for item in _cfg.get_raw().get('web', {}).get('users') or []:
        if item.get('username') == username:
            cid = item.get('conversation_id') or f"user_{username}"
            break
    if not cid:
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    from app.database import get_history
    return {"username": username, "conversation_id": cid, "messages": get_history(cid, limit=limit, offset=offset)}

@app.get("/api/user/persona/{name}/preview")
async def api_user_persona_preview(name: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")  # noqa: keep path
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    mem = []
    if _chat and user.get("role") == "user":
        mem = _chat.memory.get_recent_context(name, f"user_{user['username']}")[-10:]
    return {"name": name, "memory": mem}

@app.post("/api/admin/users/create")
async def api_admin_users_create(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    username = (req.get("username") or "").strip()
    password = (req.get("password") or "").strip()
    if not username or not password:
        return JSONResponse(status_code=400, content={"error": "内容不能为空"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    users = data.get('web', {}).get('users') or []
    for item in users:
        if item.get('username') == username:
            return JSONResponse(status_code=400, content={"error": "角色名不能为空"})
    users.append({"username": username, "password": password, "role": "user", "persona": req.get("persona") or "MONO", "conversation_id": f"user_{username}", "persona_override": {}, "persona_prompt": "", "library_access": False, "allowed_personas": None})
    _cfg.update('web.users', users)
    return {"success": True}

@app.post("/api/admin/users/delete")
async def api_admin_users_delete(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    username = (req.get("username") or "").strip()
    if not username:
        return JSONResponse(status_code=400, content={"error": "内容不能为空"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    users = data.get('web', {}).get('users') or []
    new_users = [x for x in users if x.get('username') != username]
    if len(new_users) == len(users):
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    _cfg.update('web.users', new_users)
    return {"success": True}

@app.get("/api/admin/users/{username}/memory")
async def api_admin_user_memory(username: str, request: Request, limit: int = Query(default=20)):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    from data.config import config as _cfg
    persona = None
    for item in _cfg.get_raw().get('web', {}).get('users') or []:
        if item.get('username') == username:
            persona = item.get('persona') or 'MONO'
            break
    if not persona:
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    mem = []
    if _chat:
        mem = _chat.memory.get_recent_context(persona, f"user_{username}")[-limit*2:]
    return {"username": username, "persona": persona, "memory": mem}

@app.get("/api/admin/users/{username}/persona/{name}")
async def api_admin_user_persona(username: str, name: str, request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        base = f.read()
    from data.config import config as _cfg
    override = ""
    for item in _cfg.get_raw().get('web', {}).get('users') or []:
        if item.get('username') == username:
            override = item.get('persona_override', {}).get(name, '')
            break
    return json_utf8_response({"username": username, "name": name, "base_content": base, "override_content": override})

@app.post("/api/admin/users/{username}/persona/{name}")
async def api_admin_user_persona_save(username: str, name: str, request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == username:
            item.setdefault('pending_persona_override', {})[name] = req.get('content', '')
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.get("/api/user/personas")
async def api_user_personas(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    current_persona = None
    allowed = None
    if user.get("role") == "user":
        from data.config import config as _cfg
        for item in _cfg.get_raw().get("web", {}).get("users") or []:
            if item.get("username") == user["username"]:
                current_persona = item.get("persona") or None
                allowed = item.get("allowed_personas")
                break
    result = []
    for name in sorted(os.listdir(avatar_root)):
        p2 = os.path.join(avatar_root, name, "avatar.md")
        if os.path.isfile(p2) and (allowed is None or name in allowed):
            result.append({"name": name, "active": name == (current_persona or (_chat.avatar_name if _chat else None))})
    # Add user-created personas
    from data.config import config as _cfg
    for item in _cfg.get_raw().get("web", {}).get("users") or []:
        if item.get("username") == user["username"]:
            for up_name in item.get("user_personas") or {}:
                result.append({"name": up_name, "active": up_name == current_persona, "user_created": True})
            break
    return {"personas": result, "current": current_persona or (_chat.avatar_name if _chat else None)}

# ============================================================
# 以下代码从 server.cpython-311.pyc 字节码重建 ============================================================





@app.post("/api/user/persona/create")
async def api_user_persona_create(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    name = (req.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "请输入角色名称"})
    # Check global personas for duplicate
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    if os.path.isdir(os.path.join(avatar_root, name)):
        return JSONResponse(status_code=400, content={"error": "角色 " + name + " 已存在（系统角色）"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            user_personas = item.setdefault('user_personas', {})
            if name in user_personas:
                return JSONResponse(status_code=400, content={"error": "角色 " + name + " 已存在"})
            content = req.get("content", "# " + name + "\n\n你是一个友好的AI助手。")
            user_personas[name] = content
            # Also add to allowed_personas if not already
            allowed = item.setdefault('allowed_personas', [])
            if name not in allowed:
                allowed.append(name)
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.post("/api/user/persona/rename")
async def api_user_persona_rename(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    old_name = (req.get("old_name") or "").strip()
    new_name = (req.get("new_name") or "").strip()
    if not old_name or not new_name:
        return JSONResponse(status_code=400, content={"error": "请提供新旧名称"})
    if old_name == new_name:
        return JSONResponse(status_code=400, content={"error": "新旧名称相同"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
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
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.get("/api/user/persona/{name}")
async def api_user_persona_detail(name: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    # Check user-created personas first
    from data.config import config as _cfg
    user_override = ""
    persona_prompt_val = ""
    pending_override = ""
    if user.get("role") == "user":
        for item in _cfg.get_raw().get('web', {}).get('users') or []:
            if item.get('username') == user['username']:
                user_personas = item.get('user_personas') or {}
                if name in user_personas:
                    user_override = item.get('persona_override', {}).get(name, '')
                    persona_prompt_val = item.get('persona_prompt', '')
                    pending_override = item.get('pending_persona_override', {}).get(name, '')
                    return json_utf8_response({"name": name, "base_content": user_personas[name], "user_content": user_override, "persona_prompt": persona_prompt_val, "pending_content": pending_override, "user_created": True})
                user_override = item.get('persona_override', {}).get(name, '')
                persona_prompt_val = item.get('persona_prompt', '')
                pending_override = item.get('pending_persona_override', {}).get(name, '')
                break
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": f"角色 {name} 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        base = f.read()
    return json_utf8_response({"name": name, "base_content": base, "user_content": user_override, "persona_prompt": persona_prompt_val, "pending_content": pending_override})

@app.post("/api/user/persona/reset")
async def api_user_persona_reset(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    name = (req.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "请提供角色名称"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            override = item.get('persona_override') or {}
            if name in override:
                del override[name]
                item['persona_override'] = override
                _cfg.update('web.users', data['web']['users'])
            if item.get('persona_prompt'):
                item['persona_prompt'] = ''
                _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})


@app.post("/api/user/persona/select")
async def api_user_persona_select(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    name = (req.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "请提供角色名称"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
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
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.post("/api/user/persona/save")
async def api_user_persona_save(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    name = (req.get("name") or "").strip()
    content = (req.get("content") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "请提供角色名称"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            # Check allowed_personas
            allowed = item.get('allowed_personas')
            if allowed is not None and name not in allowed:
                return JSONResponse(status_code=403, content={"error": "你无权访问此角色"})
            item.setdefault('persona_override', {})[name] = content
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    from data.config import config as _cfg
    data = _cfg.get_raw().get('web', {})
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


@app.post("/api/admin/users/update")
async def api_admin_users_update(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    target = (req.get("username") or "").strip()
    if not target:
        return JSONResponse(status_code=400, content={"error": "名称不能为空"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    users = data.get('web', {}).get('users') or []
    found = None
    for item in users:
        if item.get("username") == target:
            found = item
            break
    if not found:
        return JSONResponse(status_code=404, content={"error": "用户不存在"})
    if "password" in req and req["password"]:
        found["password"] = req["password"]
    if "persona" in req and req["persona"]:
        found["persona"] = req["persona"]
    if "persona_prompt" in req:
        found["persona_prompt"] = req["persona_prompt"]
    if "persona_override" in req:
        found["pending_persona_override"] = req["persona_override"]
    if "library_access" in req:
        found["library_access"] = bool(req["library_access"])
    if "allowed_personas" in req:
        found["allowed_personas"] = req["allowed_personas"]
    if "api_key" in req:
        found["api_key"] = req["api_key"]
    if "base_url" in req:
        found["base_url"] = req["base_url"]
    if "model" in req:
        found["model"] = req["model"]
    _cfg.update('web.users', users)
    return {"success": True}


@app.post("/api/admin/persona-override")
async def api_admin_persona_override(request: Request, req: dict):
    # 管理员保存自己的角色性格设定
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    persona = (req.get("persona") or "").strip()
    content = (req.get("content") or "").strip()
    if not persona:
        return JSONResponse(status_code=400, content={"error": "名称不能为空"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    admin = data.get('web', {}).get('admin') or {}
    overrides = admin.get('admin_persona_override') or {}
    overrides[persona] = content
    admin['admin_persona_override'] = overrides
    data['web']['admin'] = admin
    _cfg.update('web.admin', admin)
    return {"success": True}


@app.get("/api/admin/persona-library")
async def api_admin_persona_library(request: Request):
    # 获取管理员公开性格库
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    from data.config import config as _cfg
    library = _cfg.get_raw().get('web', {}).get('persona_library') or []
    return json_utf8_response({"library": library})


@app.post("/api/admin/persona-library/save")
async def api_admin_persona_library_save(request: Request, req: dict):
    # 保存/更新管理员公开性格模板
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    name = (req.get("name") or "").strip()
    content_val = req.get("content") or ""
    persona_name = req.get("persona_name") or ""
    is_public = req.get("public", True)
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不能为空"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    library = data.get('web', {}).get('persona_library') or []
    found = False
    for item in library:
        if item.get("name") == name:
            item.update({
                "name": name,
                "content": content_val,
                "persona_name": persona_name,
                "public": is_public,
            })
            found = True
            break
    if not found:
        library.append({
            "name": name,
            "content": content_val,
            "persona_name": persona_name,
            "public": is_public,
        })
    _cfg.update('web.persona_library', library)
    return {"success": True}


@app.post("/api/admin/persona-library/delete")
async def api_admin_persona_library_delete(request: Request, req: dict):
    # 删除管理员公开性格模板
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    name = (req.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不能为空"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    library = data.setdefault('web', {}).setdefault('persona_library', [])
    library = [x for x in library if x.get("name") != name]
    _cfg.update('web.persona_library', library)
    return {"success": True}


@app.get("/api/user/pending-overrides")
async def api_user_pending_overrides(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "权限不足"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    pending = {}
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            pending = item.get('pending_persona_override') or {}
            break
    return {"pending": pending}


@app.post("/api/user/approve-override")
async def api_user_approve_override(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "权限不足"})
    persona = (req.get("persona") or "").strip()
    approve = req.get("approve", True)
    if not persona:
        return JSONResponse(status_code=400, content={"error": "缺少角色名"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    for item in data.get('web', {}).get('users') or []:
        if item.get('username') == user['username']:
            pending = item.get('pending_persona_override') or {}
            if persona not in pending:
                return JSONResponse(status_code=404, content={"error": "无待审批内容"})
            if approve:
                item.setdefault('persona_override', {})[persona] = pending[persona]
            del pending[persona]
            item['pending_persona_override'] = pending
            _cfg.update('web.users', data['web']['users'])
            return {"success": True, "action": "approved" if approve else "rejected"}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})


@app.get("/api/user/persona-library")
async def api_user_persona_library(request: Request):
    # 鑾峰彇鍏紑鐨勬€ф牸妯℃澘鍒楄〃锛堢敤鎴峰彲瑙侊級
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    from data.config import config as _cfg
    library = _cfg.get_raw().get('web', {}).get('persona_library') or []
    library_access = False
    for item in _cfg.get_raw().get('web', {}).get('users') or []:
        if item.get("username") == user["username"]:
            library_access = item.get("library_access", False)
            break
    if not library_access:
        return json_utf8_response({"library": [], "access_denied": True})
    public_items = [x for x in library if x.get("public", True)]
    return json_utf8_response({"library": public_items})


@app.post("/api/user/persona/apply-library")
async def api_user_persona_apply_library(request: Request, req: dict):
    # 鐢ㄦ埛搴旂敤绠＄悊鍛樺叕寮€鐨勬€ф牸妯℃澘鍒版寚瀹氳鑹?
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    lib_name = (req.get("library_name") or "").strip()
    persona = req.get("persona") or ""
    if not lib_name or not persona:
        return JSONResponse(status_code=400, content={"error": "请提供角色名称"})
    from data.config import config as _cfg
    data = _cfg.get_raw()
    library = data.get('web', {}).get('persona_library') or []
    lib_item = None
    for x in library:
        if x.get("name") == lib_name and x.get("public", True):
            lib_item = x
            break
    if not lib_item:
        return JSONResponse(status_code=404, content={"error": "??"})
    for item in data.get('web', {}).get('users') or []:
        if item.get("username") == user["username"]:
            item.setdefault('persona_override', {})[persona] = lib_item.get("content", "")
            _cfg.update('web.users', data['web']['users'])
            return {"success": True}
    return JSONResponse(status_code=404, content={"error": "用户不存在"})

@app.post("/api/chat")
async def api_chat(request: Request, req: ChatReq):
    if not _chat:
        return JSONResponse(status_code=404, content={"error": "服务未就绪"})
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "请先登录"})
    cid = req.conversation_id
    uid = user.get("username")
    if user.get("role") == "user":
        cid = user.get("conversation_id") or cid
    logger.info(f"[HTTP??] ??=")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _chat.handle(req.message, cid, user=uid))
    if isinstance(result, tuple):
        reply, usage = result
    else:
        reply = result
        usage = {}
        logger.info(f"角色已创建: {name}")
    return {"reply": reply, "usage": usage}


@app.post("/api/chat/stream")
async def api_chat_stream(request: Request, req: ChatReq):
    """SSE streaming chat endpoint"""
    if not _chat:
        return JSONResponse(status_code=404, content={"error": "服务未就绪"})
    from fastapi.responses import StreamingResponse
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "请先登录"})
    cid = req.conversation_id
    uid = user.get("username")
    if user.get("role") == "user":
        cid = user.get("conversation_id") or cid
        logger.info(f"角色已删除: {name}")
    q = asyncio.Queue()
    _sentinel = object()

    async def _run():
        loop = asyncio.get_running_loop()

        def _produce():
            try:
                for chunk in _chat.handle_stream(req.message, cid, user=uid):
                    asyncio.run_coroutine_threadsafe(q.put(chunk), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(q.put(e), loop)
            finally:
                asyncio.run_coroutine_threadsafe(q.put(_sentinel), loop)

        await loop.run_in_executor(None, _produce)

    task = asyncio.create_task(_run())

    async def _generate():
        yield "event: start\ndata: {}\n\n"
        chunk_count = 0
        while True:
            item = await q.get()
            if item is _sentinel:
                break
            if isinstance(item, Exception):
                yield json.dumps({"event": "error", "data": str(item)}) + "\n\n"
                break
            data = json.dumps({"text": item})
            yield f"event: chunk\ndata: {data}\n\n"
            chunk_count += 1
        usage = _chat._last_stream_usage if hasattr(_chat, '_last_stream_usage') else {}
        yield f"event: end\ndata: {json.dumps({'usage': usage, 'chunks': chunk_count})}\n\n"
        logger.info(f"[SSE?] ??: chunks={chunk_count} tokens={usage}")

    async def _stream_with_cleanup():
        async for chunk in _generate():
            yield chunk
        if not task.done():
            task.cancel()

    return StreamingResponse(
        _stream_with_cleanup(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/command")
async def api_command(request: Request, req: CmdReq):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    if not _chat:
        return JSONResponse(status_code=503, content={"error": "聊天服务未就绪"})
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _chat.handle(req.command, req.conversation_id))
    if isinstance(result, tuple):
        reply, usage = result
    else:
        reply = result
        usage = {}
    return {"reply": reply, "usage": usage}


@app.post("/api/tts")
async def api_tts(req: TTSReq):
    if not _tts:
            return JSONResponse(status_code=500, content={"error": "TTS 生成失败"})
    loop = asyncio.get_running_loop()
    try:
        path = await loop.run_in_executor(None, lambda: _tts.generate(req.text))
    except Exception:
            return JSONResponse(status_code=500, content={"error": "TTS 生成失败"})
    if not path:
        return JSONResponse(status_code=500, content={"error": "TTS 生成失败"})
    return FileResponse(path, media_type="audio/mpeg", filename=os.path.basename(path))


@app.get("/api/stats")
async def api_stats(request: Request):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    from data.config import config as _cfg
    from app.database import get_message_count
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    persona_count = 0
    try:
        persona_count = len([n for n in os.listdir(avatar_root) if os.path.isdir(os.path.join(avatar_root, n)) and os.path.isfile(os.path.join(avatar_root, n, "avatar.md"))])
    except Exception as e:
        logger.debug("切换角色失败: " + str(e))
    api_key = ""
    if hasattr(_chat, 'llm') and _chat.llm:
        api_key = getattr(_chat.llm, 'api_key', '')
    return {
        "active_persona": _chat.avatar_name if _chat else "-",
        "persona_count": persona_count,
        "total_messages": get_message_count(),
        "api_key_configured": bool(api_key),
        "ws_clients": len(_ws_clients),
        "reminder_count": len(_reminder.list_all()) if _reminder else 0,
    }


@app.get("/api/conversations")
async def api_conversations():
    from app.database import list_conversations
    return {"conversations": list_conversations()}


@app.post("/api/conversations/create")
async def api_create_conversation(req: dict):
    from app.database import create_conversation
    title = req.get("title", "通知")
    cid = create_conversation(title, _chat.avatar_name if _chat else "MONO")
    return {"conversation_id": cid}


@app.get("/api/history/{cid}")
async def api_history(cid: str, request: Request, limit: int = Query(default=50), offset: int = Query(default=0)):
    from app.database import get_history
    user = _current_user(request)
    if user and user.get("role") == "user":
        cid = user.get("conversation_id") or cid
    return {"messages": get_history(cid, limit=limit, offset=offset)}


@app.post("/api/reminders/create")
async def api_create_reminder(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    if not _reminder:
        return JSONResponse(status_code=503, content={"error": "聊天服务未就绪"})
    cid = req.get("conversation_id", "default")
    target = req.get("target_time")
    content = req.get("content", "")
    if not target or not content:
        return JSONResponse(status_code=400, content={"error": "缺少参数"})
    try:
        target_dt = datetime.fromisoformat(target)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "时间格式不合法"})
    rid = _reminder.add(cid, content, target_dt)
    return {"reminder_id": rid}


@app.post("/api/reminders/{rid}/cancel")
async def api_cancel_reminder(rid: str, request: Request):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    if not _reminder:
        return JSONResponse(status_code=503, content={"error": "聊天服务未就绪"})
    ok = _reminder.cancel(rid)
    return {"success": ok}


@app.get("/api/notifications/{cid}")
async def api_notifications(cid: str):
    from app.database import get_unread_notifications, mark_read
    items = get_unread_notifications(cid)
    mark_read(cid)
    return {"notifications": items}


@app.get("/api/personas")
async def api_personas():
    avatar_root = os.path.join(os.path.dirname(ROOT), "userdata", "avatars")
    active = _chat.avatar_name if _chat else None
    result = []
    for name in sorted(os.listdir(avatar_root)):
        p = os.path.join(avatar_root, name, "avatar.md")
        result.append({
            "name": name,
            "path": f"data/avatars/{name}",
            "has_avatar": os.path.isfile(p),
            "active": name == active,
        })
    return json_utf8_response({"personas": result})


@app.get("/api/personas/{name}")
async def api_persona_detail(name: str):
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": "角色 " + name + " 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        content = f.read()
    return json_utf8_response({"name": name, "content": content})

@app.post("/api/personas/{name}/update")
async def api_update_persona(name: str, request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": "角色 " + name + " 不存在"})
    try:
        content = req.get("content", "")
        with open(avatar_path, "w", encoding="utf-8") as f:
            f.write(content)
        if _chat and _chat.avatar_name == name:
            _chat.avatar_prompt = content
            _chat.avatar_names = _chat._extract_names(avatar_path)
        logger.info("[角色] 设定已更新? " + name)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/personas/{name}/delete")
async def api_delete_persona(name: str, request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get('role') != 'admin':
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    if not os.path.isdir(avatar_path):
        return JSONResponse(status_code=404, content={"error": "角色 " + name + " 不存在"})
    if _chat and _chat.avatar_name == name:
        return JSONResponse(status_code=400, content={"error": "不能删除褰撳墠姝ｅ湪浣跨敤鐨勮鑹"})
    try:
        import shutil
        shutil.rmtree(avatar_path)
        logger.info("[角色] 已删除? " + name)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/personas/create")
async def api_create_persona(request: Request, req: dict):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=403, content={"error": "需要登录"})
    name = (req.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "??"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    if os.path.isdir(avatar_path):
        return JSONResponse(status_code=400, content={"error": "角色 " + name + " 已存在"})
    try:
        os.makedirs(avatar_path, exist_ok=True)
        content = req.get("content", "# " + name + "\n\n浣犳槸涓€涓弸濂界殑AI鍔╂墜銆")
        with open(os.path.join(avatar_path, "avatar.md"), "w", encoding="utf-8") as f:
            f.write(content)
            logger.info("角色创建成功: " + name)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    token = (ws.query_params.get('token') or ws.cookies.get('kc_token') or '').strip()
    session = _auth_sessions.get(token)
    if not session or time.time() > session.get('expires_at', 0):
        user_info = None
    else:
        user_info = session.get('user')
    if not user_info:
        await ws.close(code=4001)
        return
    await ws.accept()
    _ws_clients.append(ws)
    client_addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "unknown"
    logger.info(f"WebSocket 客户端已连接: {client_addr} 用户={user_info.get('username')}")
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
                continue
            if not _chat:
                await ws.send_json({"type": "error", "data": "服务未就绪"})
                continue
            content = data.get("message") or ""
            cid = data.get("conversation_id") or "default"
            logger.info(f"[WS] 收到消息: 会话={cid} 长度={len(content)} 前?0字{content[:50]}{'...' if len(content) > 50 else ''}")
            q = asyncio.Queue()
            _sentinel = object()
            loop = asyncio.get_running_loop()

            def _produce():
                try:
                    for chunk in _chat.handle_stream(content, cid):
                        asyncio.run_coroutine_threadsafe(q.put(chunk), loop)
                except Exception as e:
                    asyncio.run_coroutine_threadsafe(q.put(e), loop)
                finally:
                    asyncio.run_coroutine_threadsafe(q.put(_sentinel), loop)

            task = asyncio.create_task(loop.run_in_executor(None, _produce))
            await ws.send_json({"type": "start"})
            chunk_count = 0
            try:
                while True:
                    item = await q.get()
                    if item is _sentinel:
                        break
                    if isinstance(item, Exception):
                        await ws.send_json({"type": "error", "data": str(item)})
                        break
                    await ws.send_json({"type": "chunk", "data": item})
                    chunk_count += 1
                usage = getattr(_chat, '_last_stream_usage', None) or {}
                if not usage and hasattr(_chat, 'llm'):
                    usage = getattr(_chat.llm, 'get_last_usage', lambda: {})() or {}
                if not isinstance(usage, dict):
                    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                await ws.send_json({"type": "end", "usage": usage})
                logger.info(f"[WS] ??: ")
            except Exception as e:
                try:
                    await ws.send_json({"type": "error", "data": str(e)})
                except Exception as e2:
                    logger.debug("WebSocket消息处理失败: " + str(e2))
                logger.error("[WS] 处理失败: " + str(e), exc_info=True)
    except WebSocketDisconnect:
        logger.info("WebSocket 客户端已连接: " + client_addr)
    except Exception as e:
        logger.error("[WS] 连接异常: " + str(e))
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        logger.info("[WS] 连接已清理当前连接数" + str(len(_ws_clients)))


@app.get("/api/token-usage")
async def api_token_usage():
    if _chat and _chat.llm:
        usage = _chat.llm.get_last_usage()
        return json_utf8_response({"usage": usage})
    return json_utf8_response({"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


@app.get("/api/user/home-stats")
async def api_user_home_stats(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    conversation_id = user.get("conversation_id")
    from app.database import get_message_count_for_conversation
    return json_utf8_response({
        "conversation_id": conversation_id or "",
        "persona": user.get("persona") or (_chat.avatar_name if _chat else ""),
        "message_count": get_message_count_for_conversation(conversation_id) if conversation_id else 0,
    })


@app.get("/api/personas/{name}/prompt")
async def api_persona_prompt(name: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "请先登录"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": "角色 " + name + " 不存在"})
    with open(avatar_path, "r", encoding="utf-8") as f:
        return json_utf8_response({"name": name, "content": f.read().strip()})


@app.post("/api/persona/switch")
async def api_persona_switch(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    name = (req.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不能为空"})
    full = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name)
    avatar_path = os.path.join(full, "avatar.md")
    if not os.path.isdir(full) or not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": "角色 " + name + " 不存在"})
    try:
        if _chat:
            _chat.switch_avatar(full)
            with open(avatar_path, "r", encoding="utf-8") as f:
                _chat.avatar_prompt = f.read()
            _chat.avatar_names = _chat._extract_names(full)
        from data.config import config as _cfg
        _cfg.update('behavior.avatar_dir', full)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

def _mask_key(key):
    if not key:
        return "鏈厤缃"
    if len(key) >= 4:
        return "***" + key[-4:]
    return "***"


@app.get("/api/config")
async def api_config_get(request: Request):
    from data.config import config as _cfg
    user = _current_user(request)
    data = _cfg.get_raw()
    if user and user.get("role") == "user":
        persona_prompt = ""
        for item in data.get('web', {}).get('users') or []:
            if item.get("username") == user["username"]:
                persona_prompt = item.get("persona_prompt") or ""
                break
        return json_utf8_response({
            "user_persona": data.get("behavior", {}).get("avatar_dir", ""),
            "persona_prompt": persona_prompt,
        })
    llm = data.get('llm', {})
    vision = data.get('vision', {})
    tts = data.get('tts', {})
    api_key = llm.get("api_key") or ""
    masked = _mask_key(api_key)
    return json_utf8_response({
        "api_key_masked": masked,
        "base_url": llm.get("base_url", ""),
        "model": llm.get("model", ""),
        "temperature": llm.get("temperature", 0),
        "max_tokens": llm.get("max_tokens", 0),
        "max_context_rounds": llm.get("max_context_rounds", 0),
        "vision_api_key": _mask_key(vision.get("api_key", "")),
        "vision_base_url": vision.get("base_url", ""),
        "vision_model": vision.get("model", ""),
        "admin_persona_override": data.get('web', {}).get('admin', {}).get('admin_persona_override', {}),
        "tts_api_key": _mask_key(tts.get("api_key", "")),
        "tts_base_url": tts.get("base_url", ""),
        "tts_model_id": tts.get("model_id", ""),
    })


@app.post("/api/config/save")
async def api_config_save(request: Request, req: dict):
    from data.config import config as _cfg
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    try:
        for path, value in req.items():
            _cfg.update(path, value)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/api/logs")
async def api_logs(level: str = Query(default="all"), limit: int = Query(default=100)):
    logs = get_log_buffer()
    if level and level != "all":
        logs = [x for x in logs if x.get("level") == level]
    return {"logs": logs[-limit:]}


@app.post("/api/images/recognize")
async def api_images_recognize(request: Request, file: UploadFile = File(...)):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "请先登录"})
    if not _chat:
        return JSONResponse(status_code=503, content={"error": "鏈嶅姟鏈氨缁"})
    os.makedirs(os.path.join(os.path.dirname(ROOT), "userdata", "images", "temp"), exist_ok=True)
    suffix = Path(file.filename or "image.png").suffix or ".png"
    path = os.path.join(os.path.dirname(ROOT), "userdata", "images", "temp", f"upload_{int(datetime.now().timestamp() * 1000)}{suffix}")
    data = await file.read()
    with open(path, "wb") as f:
        f.write(data)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _chat.vision.recognize(path))
    if isinstance(result, tuple):
        reply, usage = result
    else:
        reply = result
        usage = {}
    return {"recognized": reply, "usage": usage}


@app.post("/api/personas/{name}/prompt/update")
async def api_persona_update_prompt(name: str, request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要管理员权限"})
    avatar_path = os.path.join(os.path.dirname(ROOT), "userdata", "avatars", name, "avatar.md")
    if not os.path.isfile(avatar_path):
        return JSONResponse(status_code=404, content={"error": "角色 " + name + " 不存在"})
    try:
        content = req.get("content", "")
        with open(avatar_path, "w", encoding="utf-8") as f:
            f.write(content)
        if _chat and _chat.avatar_name == name:
            _chat.avatar_prompt = content
            _chat.avatar_names = _chat._extract_names(avatar_path)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/auth/verify-password")
async def api_auth_verify_password(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    from data.config import config as _cfg
    password = req.get("password", "")
    stored_pw = _cfg.web.admin.password
    if not stored_pw:
        return JSONResponse(status_code=403, content={"success": False, "error": "需要管理员权限"})
    if password == stored_pw:
        return {"authenticated": True, "success": True}
    return JSONResponse(status_code=401, content={"success": False, "error": "旧密码不正确"})


@app.post("/api/auth/reveal-key")
async def api_auth_reveal_key(request: Request, req: dict):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "需要用户登录"})
    from data.config import config as _cfg
    password = req.get("password", "")
    key_type = req.get("key_type", "llm")
    stored_pw = _cfg.web.admin.password
    if not stored_pw:
        return JSONResponse(status_code=403, content={"success": False, "error": "绠＄悊瀵嗙爜鏈缃"})
    if password != stored_pw:
        return JSONResponse(status_code=401, content={"success": False, "error": "旧密码不正确"})
    key = ""
    if key_type == "vision":
        key = getattr(_cfg.vision, 'api_key', '') or ''
    elif key_type == "tts":
        key = getattr(_cfg.tts, 'api_key', '') or ''
    else:
        key = getattr(_cfg.llm, 'api_key', '') or ''
    return {"success": True, "key": key}


@app.post("/api/auth/change-password")
async def api_auth_change_password(request: Request, req: dict):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"success": False, "error": "未登录"})
    from data.config import config as _cfg
    old_password = req.get("old_password", "")
    new_password = req.get("new_password", "")
    if not new_password or len(new_password) < 4:
        return JSONResponse(status_code=400, content={"success": False, "error": "新密码至少 4 位"})
    if user.get("role") == "admin":
        stored_pw = _cfg.web.admin.password
        if not stored_pw:
            return JSONResponse(status_code=403, content={"success": False, "error": "管理员密码未初始化"})
        if old_password != stored_pw:
            return JSONResponse(status_code=401, content={"success": False, "error": "旧密码不正确"})
        _cfg.update("web.admin.password", new_password)
        return {"success": True}
    data = _cfg.get_raw()
    users = data.get("web", {}).get("users") or []
    username = user.get("username")
    updated = False
    for item in users:
        if item.get("username") == username:
            if old_password != item.get("password", ""):
                return JSONResponse(status_code=401, content={"success": False, "error": "旧密码不正确"})
            item["password"] = new_password
            updated = True
            break
    if not updated:
        return JSONResponse(status_code=404, content={"success": False, "error": "用户不存在"})
    _cfg.update("web.users", users)
    return {"success": True}


trait_map = {
    "温柔": "gentle and tender personality",
    "活泼": "lively and energetic personality",
    "高冷": "cool and aloof personality",
    "幽默": "humorous and witty personality",
    "知性": "intellectual and knowledgeable personality",
    "元气": "cheerful and spirited personality",
    "毒舌": "sharp-tongued and sarcastic personality",
    "呆萌": "clumsy and adorable personality",
    "御姐": "mature and confident personality",
    "正经": "serious and proper personality",
    "话痨": "talkative and chatty personality",
    "安静": "quiet and reserved personality",
    "傲娇": "tsundere - cold outside but warm inside",
    "天然呆": "airheaded and naive personality",
    "腹黑": "scheming and mischievous personality",
    "中二": "chuunibyou - grandiose delusional personality",
    "社恐": "socially anxious and shy personality",
    "治愈": "healing and comforting personality",
}

@app.post("/api/generate-personality")
async def api_generate_personality(request: Request, req: dict):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "鏈櫥褰"})
    traits = req.get("traits") or []
    base_role = (req.get("base_role") or "").strip()
    custom_hint = (req.get("custom_hint") or "").strip()
    if not traits and not custom_hint:
        return JSONResponse(status_code=400, content={"error": "请至少选择一个性格特征或填写描述"})

    selected_desc = [trait_map.get(t, t) for t in traits]
    selected_text = "?".join(selected_desc)
    prompt_parts = ["你是一位AI角色设计师，请根据以下要求创建一个角色性格设定："]
    if selected_text:
        prompt_parts.append("性格特征：" + selected_text)
    if custom_hint:
        prompt_parts.append("额外要求：" + custom_hint)
    if base_role:
        prompt_parts.append("角色类型：" + base_role)
    prompt_parts.append("输出格式：\n1. 角色名字（2-3个字）\n2. 性格描述（2-3句话）\n3. 语气风格\n4. 行为禁忌\n\n直接输出角色设定，不超过200字。")
    gen_prompt = chr(10).join(prompt_parts)
    try:
        from data.config import config as _cfg
        api_key = ""
        base_url_val = ""
        model_name = ""
        if user.get("role") == "user":
            for item in _cfg.get_raw().get('web', {}).get('users') or []:
                if item.get("username") == user["username"]:
                    api_key = item.get("api_key", "")
                    base_url_val = item.get("base_url", "")
                    model_name = item.get("model", "")
                    break
        if user.get("role") == "admin":
            admin_cfg = _cfg.get_raw().get('web', {}).get('admin') or {}
            api_key = api_key or admin_cfg.get("api_key", "")
            base_url_val = base_url_val or admin_cfg.get("base_url", "")
            model_name = model_name or admin_cfg.get("model", "")
        if not api_key:
            api_key = getattr(_cfg.llm, 'api_key', '')
        if not base_url_val:
            base_url_val = getattr(_cfg.llm, 'base_url', '')
        if not model_name:
            model_name = getattr(_cfg.llm, 'model', '')
        import openai as _openai
        _client = _openai.OpenAI(api_key=api_key, base_url=base_url_val)
        _resp = _client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": gen_prompt}],
            temperature=0.8,
            max_tokens=300,
        )
        result = _resp.choices[0].message.content.strip()
        return {"success": True, "content": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"生成失败: {str(e)}"})
        return JSONResponse(status_code=500, content={"error": "生成失败: " + str(e)})


