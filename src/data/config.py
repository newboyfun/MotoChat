"""
MotoChat 配置模块
JSON 配置文件 + 属性访问 + 自动创建默认配置 + 环境变量覆盖 + .env 支持
"""
import os, json, logging, hashlib, secrets as _secrets, threading, hmac

logger = logging.getLogger("motochat")
# ROOT_DIR = src/data/ -> src/ -> new/ (project root)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "config.json")  # default, overridden by config_dir


def _load_dotenv():
    """加载项目根目录的 .env 文件到 os.environ（不覆盖已存在的环境变量）。
    无第三方依赖，仅解析 KEY=VALUE 简单格式，忽略注释和空行。"""
    env_path = os.path.join(ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        logger.info("已加载 .env 文件")
    except Exception as e:
        logger.warning(f"加载 .env 失败: {e}")


_load_dotenv()

# 环境变量优先级高于 config.json
_ENV_MAP = {
    "llm.api_key": "MOTOCHAT_LLM_API_KEY",
    "llm.base_url": "MOTOCHAT_LLM_BASE_URL",
    "llm.model": "MOTOCHAT_LLM_MODEL",
    "vision.api_key": "MOTOCHAT_VISION_API_KEY",
    "vision.base_url": "MOTOCHAT_VISION_BASE_URL",
    "vision.model": "MOTOCHAT_VISION_MODEL",
    "tts.api_key": "MOTOCHAT_TTS_API_KEY",
    "tts.model_id": "MOTOCHAT_TTS_MODEL_ID",
    "web.host": "MOTOCHAT_HOST",
    "web.port": "MOTOCHAT_PORT",
    "web.secret": "MOTOCHAT_SECRET",
}

def _getenv(path, default=None):
    """按配置路径查找环境变量，有则返回(取值, True)"""
    env_name = _ENV_MAP.get(path)
    if env_name:
        val = os.environ.get(env_name)
        if val is not None and val != "":
            target_type = type(default)
            if target_type is int:
                try:
                    return int(val), True
                except ValueError:
                    pass
            elif target_type is float:
                try:
                    return float(val), True
                except ValueError:
                    pass
            return val, True
    return default, False

# ─── Password hashing ─────────────────────────────────────────────
_PBKDF2_ITERATIONS = 310000
_PBKDF2_PREFIX = "$pbkdf2-sha256$"

def hash_password(password: str) -> str:
    if not password:
        return ""
    salt = _secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_PREFIX}{_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"

def verify_password(password: str, stored: str) -> bool:
    if not password or not stored:
        return False
    if not stored.startswith(_PBKDF2_PREFIX):
        # 兼容旧格式：明文比较（恒定时间）
        return hmac.compare_digest(password, stored)
    try:
        parts = stored.split("$")
        # parts = ['', 'pbkdf2-sha256', iterations, salt_hex, dk_hex]
        if len(parts) != 5:
            return False
        iterations = int(parts[2])
        salt = bytes.fromhex(parts[3])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk.hex(), parts[4])
    except (ValueError, IndexError):
        return False

def is_password_hashed(stored: str) -> bool:
    return stored.startswith(_PBKDF2_PREFIX) if stored else True

DEFAULT_CONFIG = {
    "llm": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "max_tokens": 2000,
        "temperature": 1.1,
        "max_context_rounds": 15,
        "auto_model_switch": False
    },
    "vision": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.7
    },
    "tts": {
        "api_key": "",
        "model_id": "",
        "base_url": "https://api.openai.com/v1",
        "voice_dir": "data/voices"
    },
    "behavior": {
        "avatar_dir": "data/avatars/MONO",
        "auto_message": {"content": "在干嘛呢~", "min_hours": 1.0, "max_hours": 3.0},
        "quiet_time": {"start": "22:00", "end": "08:00"},
        "queue_timeout": 8
    },
    # 密码留空：首次运行时自动生成随机密码并打印到控制台（不再内置弱口令）
    "web": {"host": "127.0.0.1", "port": 7860, "admin_password": "", "secret": "", "admin": {"username": "admin", "password": ""}, "users": [{"username": "user", "password": "", "role": "user", "persona": "MONO", "conversation_id": "user_user"}]}
}


class _Section:
    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(key)
        try:
            val = self._data[key]
        except KeyError:
            raise AttributeError(f"Config has no attribute '{key}'")
        return _Section(val) if isinstance(val, dict) else val

    def __setattr__(self, key, value):
        if key == "_data":
            super().__setattr__(key, value)
        else:
            self._data[key] = value

    def get(self, key, default=None):
        val = self._data.get(key, default)
        return _Section(val) if isinstance(val, dict) else val


class ConfigError(ValueError):
    # 继承 ValueError：非法配置值属于值错误，方便调用方用 ValueError 捕获
    pass


class Config:
    def __init__(self, config_dir=None):
        global CONFIG_PATH
        if config_dir:
            CONFIG_PATH = os.path.join(config_dir, "config.json")
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        # 记录被环境变量覆盖的配置项原始值，save 时还原，避免 .env 秘密被写入 config.json
        self._env_backup = {}
        self._data = self._load()
        self._validate()
        self._write_lock = threading.Lock()  # 保护并发写入

    def _load(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
                data = {}
            merged = self._deep_merge(DEFAULT_CONFIG, data)
            merged, changed = self._apply_user_defaults(merged)
            if not merged.get('web', {}).get('secret'):
                merged.setdefault('web', {})['secret'] = _secrets.token_hex(24)
                changed = True
            # 密码缺失时生成随机密码（只打印一次）
            if self._ensure_passwords(merged):
                changed = True
            # 自动哈希旧格式明文密码（注意：必须先调用再判断，or 短路会跳过迁移）
            migrated = self._migrate_passwords(merged)
            if changed or migrated:
                self._save(merged)
            # 环境变量覆盖
            self._apply_env_overrides(merged)
            return merged
        # 首次运行：生成随机密码并哈希后写入，避免弱口令/明文密码落盘
        import copy
        initial = copy.deepcopy(DEFAULT_CONFIG)
        self._ensure_passwords(initial)
        self._migrate_passwords(initial)
        initial.setdefault('web', {})['secret'] = _secrets.token_hex(24)
        self._save(initial)
        self._apply_env_overrides(initial)
        return initial

    def _ensure_passwords(self, data: dict) -> bool:
        """为密码为空的账号生成随机密码（打印到控制台，仅一次），返回是否有改动"""
        changed = False
        web = data.get('web', {})
        accounts = []
        admin = web.get('admin') or {}
        if admin:
            accounts.append(admin)
        accounts.extend(web.get('users') or [])
        for acc in accounts:
            if not acc.get('password'):
                pwd = _secrets.token_urlsafe(12)
                acc['password'] = hash_password(pwd)
                changed = True
                msg = f"账号 [{acc.get('username', '?')}] 已生成随机密码: {pwd} （仅显示一次，请登录后尽快修改）"
                logger.warning(msg)
                print(f"[MotoChat] {msg}")
        return changed

    def _migrate_passwords(self, data: dict) -> bool:
        """自动将明文密码转哈希，返回是否改了"""
        changed = False
        web = data.get('web', {})
        admin = web.get('admin') or {}
        if admin.get('password') and not is_password_hashed(admin['password']):
            admin['password'] = hash_password(admin['password'])
            changed = True
            logger.info("已自动哈希管理员密码")
        for u in web.get('users') or []:
            if u.get('password') and not is_password_hashed(u['password']):
                u['password'] = hash_password(u['password'])
                changed = True
                logger.info(f"已自动哈希用户 {u.get('username')} 密码")
        return changed

    def _apply_env_overrides(self, data: dict):
        """用环境变量覆盖配置中的敏感/部署字段（记录原值，save 时还原，防止 .env 秘密落盘）"""
        self._env_backup = {}
        for config_path, _env_name in _ENV_MAP.items():
            keys = config_path.split(".")
            d = data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            val, found = _getenv(config_path, d.get(keys[-1]))
            if found:
                self._env_backup[config_path] = d.get(keys[-1])
                d[keys[-1]] = val

    def _apply_user_defaults(self, data: dict) -> tuple[dict, bool]:
        import copy
        changed = False
        web = data.get('web', {})
        if not web.get('admin'):
            # 深拷贝，避免后续密码哈希原地污染 DEFAULT_CONFIG
            data.setdefault('web', {})['admin'] = copy.deepcopy(DEFAULT_CONFIG['web']['admin'])
            changed = True
        if not web.get('users'):
            data.setdefault('web', {})['users'] = copy.deepcopy(DEFAULT_CONFIG['web']['users'])
            changed = True
        return data, changed

    def _save(self, data=None):
        import tempfile, copy
        if data is None:
            data = self._data
            # 还原被环境变量覆盖的字段为文件原值，避免 .env 中的秘密被持久化
            backup = getattr(self, "_env_backup", None)
            if backup:
                data = copy.deepcopy(data)
                for path, orig in backup.items():
                    keys = path.split(".")
                    d = data
                    for k in keys[:-1]:
                        d = d.setdefault(k, {})
                    d[keys[-1]] = orig
        dirname = os.path.dirname(CONFIG_PATH)
        fd, tmp_path = tempfile.mkstemp(dir=dirname, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_PATH)
        except Exception as e:
            logger.debug(f"配置保存失败: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _deep_merge(self, d, o):
        r = d.copy()
        for k, v in o.items():
            if k in r and isinstance(r[k], dict) and isinstance(v, dict):
                r[k] = self._deep_merge(r[k], v)
            else:
                r[k] = v
        return r

    def save(self):
        with self._write_lock:
            self._save()

    def _validate(self):
        errors = []

        def require(path, types, *, allow_empty=False):
            keys = path.split(".")
            d = self._data
            for k in keys:
                if not isinstance(d, dict) or k not in d:
                    errors.append(f"缺少配置项: {path}")
                    return
                d = d[k]
            if not allow_empty and d in ("", None):
                errors.append(f"配置项为空: {path}")
            if not isinstance(d, tuple(types)):
                errors.append(f"配置项类型错误: {path}, 期望 {types}, 实际 {type(d)}")

        def require_numeric(path, min_val=None, max_val=None, *, allow_empty=False):
            keys = path.split(".")
            d = self._data
            for k in keys:
                if not isinstance(d, dict) or k not in d:
                    errors.append(f"缺少配置项: {path}")
                    return
                d = d[k]
            if allow_empty and d in ("", None):
                return
            if not isinstance(d, (int, float)):
                errors.append(f"配置项应为数字: {path}")
                return
            if min_val is not None and d < min_val:
                errors.append(f"配置项过小: {path}={d}, 最小={min_val}")
            if max_val is not None and d > max_val:
                errors.append(f"配置项过大: {path}={d}, 最大={max_val}")

        require("web.host", [str])
        require_numeric("web.port", 1, 65535)
        require("behavior.avatar_dir", [str])
        require("llm.base_url", [str])
        require("llm.model", [str])
        require_numeric("llm.max_tokens", 1, 100000)
        require_numeric("llm.temperature", 0, 2)
        require_numeric("llm.max_context_rounds", 1, 200)
        require("vision.base_url", [str], allow_empty=True)
        require("vision.model", [str], allow_empty=True)
        require("tts.voice_dir", [str])
        require("behavior.quiet_time.start", [str])
        require("behavior.quiet_time.end", [str])
        require_numeric("behavior.auto_message.min_hours", 0.01, 24)
        require_numeric("behavior.auto_message.max_hours", 0.01, 24)

        avatar_dir = os.path.join(ROOT_DIR, "userdata", "avatars", os.path.basename(self._data.get("behavior", {}).get("avatar_dir", "MONO")))
        if not os.path.isdir(avatar_dir):
            errors.append(f"角色目录不存在: {avatar_dir}")

        if errors:
            joined = "\n - ".join(errors)
            raise ConfigError(f"配置校验失败:\n - {joined}")

    @property
    def llm(self): return _Section(self._data["llm"])
    @property
    def vision(self): return _Section(self._data["vision"])
    @property
    def tts(self): return _Section(self._data["tts"])
    @property
    def behavior(self): return _Section(self._data["behavior"])
    @property
    def web(self): return _Section(self._data["web"])

    def get_raw(self): return self._data

    def update(self, path, value):
        with self._write_lock:
            keys = path.split(".")
            d = self._data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            _missing = object()
            old = d.get(keys[-1], _missing)
            d[keys[-1]] = value
            # 写入前校验，非法值回滚，避免脏配置落盘
            try:
                self._validate()
            except ConfigError:
                if old is _missing:
                    d.pop(keys[-1], None)
                else:
                    d[keys[-1]] = old
                raise
            # 显式更新的字段不再视为 env 覆盖，允许新值落盘
            self._env_backup.pop(path, None)
            self._save()

    def update_many(self, items):
        """批量更新多个配置项：只校验一次、只落盘一次，且保证全有或全无。

        items: 可迭代的 (path, value) 对。

        对比逐个调用 update()：N 个字段 = N 次 _validate() + N 次全量重写
        config.json（temp 文件 + os.replace）。前端一次保存十几个字段时开销明显。
        更重要的是原子性：逐个 update() 时若第 k 个字段非法，前 k-1 个已经落盘，
        会留下半成品配置；这里任一字段非法则整体回滚并抛 ConfigError。
        """
        items = list(items)
        if not items:
            return
        with self._write_lock:
            _missing = object()
            backup = []  # [(keys, 旧值或_missing)]
            for path, value in items:
                keys = path.split(".")
                d = self._data
                for k in keys[:-1]:
                    d = d.setdefault(k, {})
                backup.append((keys, d.get(keys[-1], _missing)))
                d[keys[-1]] = value
            try:
                self._validate()
            except ConfigError:
                # 整体回滚到写入前的状态
                for keys, old in backup:
                    d = self._data
                    for k in keys[:-1]:
                        d = d.setdefault(k, {})
                    if old is _missing:
                        d.pop(keys[-1], None)
                    else:
                        d[keys[-1]] = old
                raise
            for path, _ in items:
                self._env_backup.pop(path, None)
            self._save()


_instance = None
_instance_lock = threading.Lock()

def get_config(config_dir=None) -> "Config":
    """懒加载单例：首次调用时实例化，之后所有调用拿到同一实例。
    config_dir 仅在首次调用时生效，避免双实例 Bug。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            # 双重检查：获取锁后再次检查，避免并发创建
            if _instance is None:
                _instance = Config(config_dir=config_dir)
    return _instance

def reset_config():
    """测试用：重置单例"""
    global _instance
    with _instance_lock:
        _instance = None


