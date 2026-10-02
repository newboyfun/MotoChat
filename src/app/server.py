"""
MotoChat FastAPI 服务器 — 基础设施层
职责：应用生命周期、中间件、路由注册、页面路由、服务注入
所有 API 端点（含 WebSocket）已拆分到 app/api/ 路由模块
"""
import os, asyncio, logging, sys, secrets, time, threading
from datetime import datetime
from collections import deque

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.deps import set_service, get_current_user
from app.core.utils import ROOT, STATIC

logger = logging.getLogger("motochat")


def _current_user(request):
    return get_current_user(request)


# ===== 日志配置 =====

# 日志目录
LOG_DIR = os.path.join(os.path.dirname(ROOT), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

class _DualHandler(logging.Handler):
    """三通道(控制台+网页+文件)"""
    def __init__(self):
        super().__init__()
        self._console = logging.StreamHandler(sys.stdout)
        self._console.setFormatter(logging.Formatter(
            "  [%(asctime)s] %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
        self._buffer = deque(maxlen=2000)
        # 文件日志：按大小轮转
        from logging.handlers import RotatingFileHandler
        self._file = RotatingFileHandler(
            os.path.join(LOG_DIR, "motochat.log"),
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8"
        )
        self._file.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        try:
            # 先格式化一次，避免重复格式化
            console_msg = self._console.format(record)
            # 使用格式化后的消息写入控制台和文件
            self._console.stream.write(console_msg + self._console.terminator)
            self._console.flush()
            self._file.emit(record)
            self._buffer.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname.lower(),
                "message": console_msg.strip()
            })
        except Exception as e:
            logger.debug(f"日志写入失败: {e}")

_log_handler = _DualHandler()
logging.getLogger("motochat").addHandler(_log_handler)
logging.getLogger("motochat").setLevel(logging.DEBUG)
logging.getLogger("uvicorn").addHandler(_log_handler)
logging.getLogger("uvicorn.access").addHandler(_log_handler)
logging.getLogger("uvicorn.error").addHandler(_log_handler)


def add_log(level, msg):
    """向日志系统写入一条消息"""
    getattr(logger, level, logger.info)(msg)


def get_log_buffer():
    return _log_handler._buffer


# ===== HTML 缓存 =====

_html_cache = {}  # {name: (mtime, content)}


def _load_html_cache():
    """启动时缓存 HTML 文件到内存"""
    for name in ['login.html', 'index.html', 'user.html']:
        _reload_html(name)


def _reload_html(name):
    """检测文件修改时间，返回最新内容"""
    path = os.path.join(STATIC, name)
    try:
        mtime = os.path.getmtime(path)
        cached = _html_cache.get(name)
        if cached and cached[0] == mtime:
            return  # 文件未变，跳过
        with open(path, 'r', encoding='utf-8') as f:
            _html_cache[name] = (mtime, f.read())
        logger.debug(f"HTML 缓存已加载: {name}")
    except Exception as e:
        logger.warning(f"HTML 缓存加载失败: {name} - {e}")


def _get_html(name):
    """获取 HTML 内容：检查文件修改时间，返回最新内容"""
    _reload_html(name)
    cached = _html_cache.get(name)
    return cached[1] if cached else "Not Found"


# ===== 服务注入（单轨制：只写入 deps._services，不再维护模块级全局变量） =====

from app.core.deps import get_tts_service, get_reminder_service, get_autosend_service
# broadcast 由 run.py 从这里再导出（run.py: from app.server import ... broadcast）
from app.api.ws import broadcast  # noqa: F401


def inject(chat_service, tts_service, reminder_service, autosend_service=None):
    """注入服务实例到依赖注入容器（deps._services）"""
    set_service("chat", chat_service)
    set_service("tts", tts_service)
    set_service("reminder", reminder_service)
    set_service("autosend", autosend_service)


# ===== 应用生命周期 =====

async def _tts_cleanup_loop(tts_service):
    """后台循环：每小时清理一次过期 TTS 文件，防止 voices 目录无限增长"""
    while True:
        await asyncio.sleep(3600)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, tts_service.cleanup_old, 24)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"TTS 定期清理失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        _ensure_default_users()
    except Exception as e:
        logger.warning(f"启动默认用户初始化失败: {e}")
    # TTS：启动时先清一次过期文件，再挂每小时清理任务
    _tts_cleanup_task = None
    _tts = get_tts_service()
    if _tts and hasattr(_tts, 'cleanup_old'):
        try:
            _tts.cleanup_old(max_age_hours=24)
            _tts_cleanup_task = asyncio.create_task(_tts_cleanup_loop(_tts))
            logger.info("TTS 过期文件定期清理已启动（每小时）")
        except Exception as e:
            logger.warning(f"TTS 清理任务启动失败: {e}")
    _reminder = get_reminder_service()
    _autosend = get_autosend_service()
    try:
        loop = asyncio.get_running_loop()
        if _reminder and hasattr(_reminder, 'set_event_loop'):
            _reminder.set_event_loop(loop)
        if _autosend and hasattr(_autosend, 'set_event_loop'):
            _autosend.set_event_loop(loop)
        logger.info("事件循环已注入提醒/自动消息服务")
    except Exception as e:
        logger.warning(f"事件循环注入失败: {e}")
    # 确保 autosend 无论入口如何都能启动
    if _autosend and hasattr(_autosend, 'start') and not _autosend._running:
        _autosend.start()
        logger.info("自动消息服务已在 lifespan 中启动")
    _load_html_cache()
    yield
    # Shutdown - 优雅停止后台服务
    logger.info("服务器正在关闭...")
    if _tts_cleanup_task:
        _tts_cleanup_task.cancel()
        try:
            await _tts_cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info("TTS 定期清理任务已停止")
    try:
        if _autosend and hasattr(_autosend, 'stop'):
            _autosend.stop()
            logger.info("自动消息服务已停止")
    except Exception as e:
        logger.warning(f"停止自动消息服务失败: {e}")
    try:
        if _reminder and hasattr(_reminder, 'stop'):
            _reminder.stop()
            logger.info("提醒服务已停止")
    except Exception as e:
        logger.warning(f"停止提醒服务失败: {e}")


# ===== 应用初始化 =====

app = FastAPI(title="MotoChat", version="1.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)

# 注册全局异常处理器
from app.core.responses import register_error_handlers
register_error_handlers(app)


# ===== 注册路由模块 =====

from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.personas import router as personas_router
from app.api.user_persona import router as user_persona_router
from app.api.chat import router as chat_router
from app.api.config_routes import router as config_router
from app.api.conversations import router as conversations_router
from app.api.ws import router as ws_router

app.include_router(auth_router, prefix="/api/auth", tags=["认证"])
app.include_router(admin_router, prefix="/api/admin", tags=["管理员"])
app.include_router(personas_router, prefix="/api/personas", tags=["角色管理"])
app.include_router(user_persona_router, prefix="/api", tags=["用户角色"])
app.include_router(chat_router, prefix="/api/chat", tags=["聊天"])
app.include_router(config_router, prefix="/api", tags=["配置"])
app.include_router(conversations_router, prefix="/api/conversations", tags=["会话"])
app.include_router(ws_router, tags=["WebSocket"])

# 兼容旧版前端路径：/api/history/{cid} → /api/conversations/{cid}
@app.get("/api/history/{cid}")
async def _legacy_history_redirect(cid: str, request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    query = str(request.url.query)
    target = f"/api/conversations/{cid}" + (f"?{query}" if query else "")
    return RedirectResponse(url=target, status_code=307)

# 兼容旧版前端路径：/api/command → /api/chat/command
@app.post("/api/command")
async def _legacy_command_redirect(request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/chat/command", status_code=307)

# 兼容旧版前端路径：/api/tts → /api/chat/tts
@app.post("/api/tts")
async def _legacy_tts_redirect(request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/chat/tts", status_code=307)

# 兼容旧版前端路径：/api/images/recognize → /api/chat/images/recognize
@app.post("/api/images/recognize")
async def _legacy_images_redirect(request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/chat/images/recognize", status_code=307)

# 兼容旧版前端路径：/api/generate-personality → /api/chat/generate-personality
@app.post("/api/generate-personality")
async def _legacy_generate_personality_redirect(request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/chat/generate-personality", status_code=307)

# 兼容旧版前端路径：/api/persona/switch → /api/personas/switch
@app.post("/api/persona/switch")
async def _legacy_persona_switch_redirect(request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/personas/switch", status_code=307)

# 兼容旧版前端路径：/api/notifications/{cid} → /api/conversations/notifications/{cid}
@app.get("/api/notifications/{cid}")
async def _legacy_notifications_redirect(cid: str, request: Request):
    """兼容旧版前端路径"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/api/conversations/notifications/{cid}", status_code=307)

app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ===== 中间件 =====

# 简单的内存限流器（IP -> 请求记录）
_rate_limit_cache = {}
_rate_limit_lock = threading.Lock()

def _check_rate_limit(ip: str, limit: int = 30, window: int = 60) -> bool:
    """检查IP是否超过限流限制"""
    now = time.time()
    with _rate_limit_lock:
        if ip not in _rate_limit_cache:
            _rate_limit_cache[ip] = []
        # 清理过期记录
        _rate_limit_cache[ip] = [t for t in _rate_limit_cache[ip] if now - t < window]
        if len(_rate_limit_cache[ip]) >= limit:
            return False
        _rate_limit_cache[ip].append(now)
        # 定期清理过期条目，防止字典无限增长
        if len(_rate_limit_cache) > 1000:
            expired_ips = [k for k, times in _rate_limit_cache.items() if not any(now - t < window for t in times)]
            for k in expired_ips:
                del _rate_limit_cache[k]
        return True

@app.middleware("http")
async def rate_limit_middleware(request, call_response):
    """聊天API限流中间件（仅对 HTTP 生效；WS 消息级限流在 api/ws.py 内单独实现）"""
    path = request.url.path
    # 只对聊天API进行限流
    if path.startswith("/api/chat"):
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip, limit=30, window=60):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"error": "请求过于频繁，请稍后再试"}
            )
    return await call_response(request)

@app.middleware("http")
async def add_cache_headers(request, call_response):
    response = await call_response(request)
    path = request.url.path

    if path.startswith("/static/"):
        if '?v=' in str(request.url):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif any(path.endswith(ext) for ext in ['.js', '.css']):
            response.headers["Cache-Control"] = "public, max-age=3600"
        elif any(path.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']):
            response.headers["Cache-Control"] = "public, max-age=86400"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
    elif path.endswith('.html') or path in ['/', '/login', '/admin', '/user']:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    return response


# ===== 页面路由 =====

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_get_html('login.html'))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = _current_user(request)
    if not user:
        return HTMLResponse(_get_html('login.html'))
    if user.get("role") == "admin":
        return HTMLResponse(_get_html('index.html'))
    return HTMLResponse(_get_html('user.html'))


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login", status_code=302)
    resp = HTMLResponse(_get_html('index.html'))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/user", response_class=HTMLResponse)
async def user_app(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "user":
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(_get_html('user.html'))


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """旧路径兼容：admin-users.html 已删除，重定向到主页面（用户管理已内嵌）"""
    return RedirectResponse(url="/admin", status_code=302)


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/api/health")
async def health_check():
    """健康检查端点（无需认证）"""
    return {"status": "ok"}


# ===== 启动辅助 =====

def _ensure_default_users():
    """确保默认用户存在（仅在用户完全缺失时创建，绝不重置已有密码）"""
    from data.config import get_config as _get_cfg
    from data.config import hash_password as _hpw
    cfg = _get_cfg()
    data = cfg.get_raw().get('web', {})
    changed = False
    if not data.get('admin', {}).get('username'):
        cfg.update('web.admin.username', 'admin')
        changed = True
    if not data.get('admin', {}).get('password'):
        # 密码完全缺失（配置损坏）→ 生成随机密码并打印到控制台（仅显示一次）
        # 不做“重置回弱口令”，防止管理员密码被清空后系统自动恢复成 admin123
        rand_pw = secrets.token_urlsafe(9)
        cfg.update('web.admin.password', _hpw(rand_pw))
        changed = True
        print("=" * 56)
        print("  [!] 检测到管理员密码缺失，已生成随机密码（仅显示一次）:")
        print(f"      账号: admin  密码: {rand_pw}")
        print("      请登录后立即修改密码。")
        print("=" * 56)
        logger.warning("管理员密码缺失，已生成随机密码并打印到控制台")
    # 为 admin 设置 conversation_id（与普通用户保持一致）
    if not data.get('admin', {}).get('conversation_id'):
        cfg.update('web.admin.conversation_id', 'admin')
        changed = True
    if not data.get('secret'):
        cfg.update('web.secret', secrets.token_hex(24))
        changed = True
    if not data.get('users'):
        rand_pw = secrets.token_urlsafe(9)
        cfg.update('web.users', [{'username': 'user', 'password': _hpw(rand_pw), 'role': 'user', 'conversation_id': 'user_user'}])
        changed = True
        print(f"  [!] 已创建默认普通账号: user / {rand_pw} （仅显示一次，请登录后修改）")
        logger.warning("默认普通用户已创建，随机密码已打印到控制台")
    # 上面已保证 users 存在，这里直接遍历补全（原先外层还套了一层 `if 'users' in data`，恒为真）
    for u in data.get('users') or []:
        if u.get('password') and not u['password'].startswith('$pbkdf2'):
            u['password'] = _hpw(u['password'])
            changed = True
        # 确保每个用户都有 conversation_id
        if not u.get('conversation_id'):
            u['conversation_id'] = f"user_{u.get('username')}"
            changed = True
    if changed:
        cfg.save()
    return changed

