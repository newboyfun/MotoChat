"""
MotoChat - 本地网页版 AI 角色聊天
目录分离版：src/ + userdata/ + config/
"""
import os, sys, signal, atexit, logging, socket

logger = logging.getLogger("motochat")

LOCK_PORT = 19876
_lock_socket = None


def _acquire_lock():
    global _lock_socket
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Windows 使用 SO_EXCLUSIVEADDRUSE 确保独占绑定，其他平台使用 SO_REUSEADDR
        if sys.platform == 'win32':
            SO_EXCLUSIVE = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
            if SO_EXCLUSIVE is not None:
                s.setsockopt(socket.SOL_SOCKET, SO_EXCLUSIVE, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        _lock_socket = s
        return True
    except OSError:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass
        return False


def _release_lock():
    global _lock_socket
    try:
        if _lock_socket:
            _lock_socket.close()
            _lock_socket = None
    except Exception as e:
        logger.debug(f"释放锁失败: {e}")


def _signal_handler(sig, frame):
    print("\n  [!] 收到退出信号，正在清理...")
    _release_lock()
    sys.exit(0)


# ===== 目录结构 =====
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.join(PROJECT_ROOT, "src")
DATA_ROOT = os.path.join(PROJECT_ROOT, "userdata")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")

sys.path.insert(0, SRC_ROOT)
os.chdir(PROJECT_ROOT)

for d in ["userdata/database", "userdata/voices", "userdata/images/temp", "logs"]:
    os.makedirs(os.path.join(PROJECT_ROOT, d), exist_ok=True)

# Bootstrap: copy default avatars from src/data/avatars to userdata/avatars on first run
_src_avatars = os.path.join(SRC_ROOT, "data", "avatars")
_usr_avatars = os.path.join(DATA_ROOT, "avatars")
if os.path.isdir(_src_avatars):
    os.makedirs(_usr_avatars, exist_ok=True)
    for _name in os.listdir(_src_avatars):
        _s = os.path.join(_src_avatars, _name)
        _d = os.path.join(_usr_avatars, _name)
        if os.path.isdir(_s) and not os.path.exists(_d):
            import shutil
            shutil.copytree(_s, _d)
            logger.info(f"已初始化默认角色: {_name}")


def main():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _signal_handler)

    if not _acquire_lock():
        print("  [!] 检测到已有实例在运行 (端口 19876)，请先关闭后重试")
        print("  [!] 如果确认无实例运行，请检查端口占用: netstat -ano | findstr 19876")
        sys.exit(1)
    atexit.register(_release_lock)

    import uvicorn
    try:
        from data.config import get_config
        config = get_config(config_dir=CONFIG_DIR)
    except Exception as e:
        print(f"  [X] 配置加载失败: {e}")
        sys.exit(1)
    from app.database import init_db, ensure_default
    from app.services.llm import LLMService
    from app.services.memory import MemoryService
    from app.services.vision import VisionService
    from app.services.tts import TTSService
    from app.services.chat import ChatService
    from app.services.reminder import ReminderService
    from app.services.autosend import AutoSendService
    from app.server import app, inject, broadcast, add_log

    print("=" * 56)
    print("   MotoChat v1.0")
    print("=" * 56)

    init_db(db_dir=os.path.join(DATA_ROOT, "database"))

    print(f"  [INIT] 模型: {config.llm.model}")

    llm = LLMService(
        api_key=config.llm.api_key, base_url=config.llm.base_url,
        model=config.llm.model, max_tokens=config.llm.max_tokens,
        temperature=config.llm.temperature, max_context_rounds=config.llm.max_context_rounds,
        auto_model_switch=config.llm.auto_model_switch)

    memory = MemoryService(root_dir=DATA_ROOT, llm_service=llm, max_groups=config.llm.max_context_rounds, src_root=SRC_ROOT)

    vision = VisionService(
        api_key=config.vision.api_key or config.llm.api_key,
        base_url=config.vision.base_url or config.llm.base_url,
        model=config.vision.model, temperature=config.vision.temperature)

    tts = TTSService(api_key=config.tts.api_key, model_id=config.tts.model_id,
                     base_url=getattr(config.tts, "base_url", None),
                     voice_dir=os.path.join(DATA_ROOT, "voices"))

    chat = ChatService(SRC_ROOT, DATA_ROOT, config, llm, memory, vision, tts)
    ensure_default(chat.avatar_name)

    reminder = ReminderService(chat, broadcast_fn=broadcast)
    autosend = AutoSendService(config, chat, broadcast_fn=broadcast)
    inject(chat, tts, reminder, autosend)

    add_log("info", "系统启动完成")
    add_log("info", f"默认角色: {chat.avatar_name}")

    # autosend.start() 已移至 server.py 的 lifespan 中，与 set_event_loop 一起调用
    # 确保时序上完全确定：先设置 event loop，再启动 autosend

    host = config.web.host
    port = config.web.port
    print(f"  [OK] 请访问: http://{host}:{port}")
    print()

    try:
        uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)
    finally:
        _release_lock()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _release_lock()
        print("\n  [OK] 再见!")
