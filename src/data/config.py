"""
MotoChat 配置模块
JSON 配置文件 + 属性访问 + 自动创建默认配置
"""
import os, json, logging

logger = logging.getLogger("motochat")
# ROOT_DIR = src/data/ -> src/ -> new/ (project root)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "config.json")  # default, overridden by config_dir

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
    "web": {"host": "127.0.0.1", "port": 7860, "admin_password": "", "secret": "", "admin": {"username": "admin", "password": "admin123"}, "users": [{"username": "user", "password": "user1234", "role": "user", "persona": "MONO", "conversation_id": "user_default"}]}
}


class _Section:
    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, key):
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


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, config_dir=None):
        global CONFIG_PATH
        if config_dir:
            CONFIG_PATH = os.path.join(config_dir, "config.json")
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        self._data = self._load()
        self._validate()

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
                merged.setdefault('web', {})['secret'] = __import__('secrets').token_hex(24)
                changed = True
            if changed:
                self._save(merged)
            return merged
        self._save(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    def _apply_user_defaults(self, data: dict) -> tuple[dict, bool]:
        changed = False
        web = data.get('web', {})
        if not web.get('admin'):
            data.setdefault('web', {})['admin'] = DEFAULT_CONFIG['web']['admin']
            changed = True
        if not web.get('users'):
            data.setdefault('web', {})['users'] = DEFAULT_CONFIG['web']['users']
            changed = True
        return data, changed
    def get_user_credentials(self):
        admin = self._data.get("web", {}).get("admin") or {}
        users = self._data.get("web", {}).get("users") or []
        return {"admin": admin, "users": users}
    def _save(self, data=None):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data or self._data, f, ensure_ascii=False, indent=2)

    def _deep_merge(self, d, o):
        r = d.copy()
        for k, v in o.items():
            if k in r and isinstance(r[k], dict) and isinstance(v, dict):
                r[k] = self._deep_merge(r[k], v)
            else:
                r[k] = v
        return r

    def save(self):
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
        keys = path.split(".")
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self._save()


config = Config()

