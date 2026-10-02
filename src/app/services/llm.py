"""
LLM 服务 - 无状态封装 OpenAI 兼容 API
上下文管理交给调用方（MemoryService），本服务只负责 API 调用。
"""
import re, time, logging, collections, threading
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

# httpx 用于设置 OpenAI 客户端超时
try:
    import httpx
except ImportError:
    httpx = None

# 表情标签（延迟导入，避免循环依赖）
_EMOTION_TAGS = None
_EMOTION_RE = None

def _load_emotion_consts():
    global _EMOTION_TAGS, _EMOTION_RE
    if _EMOTION_TAGS is None:
        from app.services.emoji import EMOTION_TAGS, EMOTION_RE
        _EMOTION_TAGS = EMOTION_TAGS
        _EMOTION_RE = EMOTION_RE

logger = logging.getLogger("motochat")


@dataclass
class LLMResult:
    reply: str
    usage: Dict[str, int]
    error: str = ""


def _empty_usage() -> Dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class LLMService:
    def __init__(self, api_key, base_url, model, max_tokens=2000,
                 temperature=1.1, max_context_rounds=15, auto_model_switch=False):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_context_rounds = max_context_rounds
        self.auto_model_switch = auto_model_switch
        self.client = None
        self._client_key = None  # 跟踪客户端的 (api_key, base_url) 用于检测配置变更
        # 用户客户端 LRU 缓存：{(api_key, base_url): (client, timestamp)}
        self._user_clients = collections.OrderedDict()
        self._user_clients_max = 30   # 最大缓存数（本地场景足够）
        self._user_clients_ttl = 3600  # TTL: 1小时未使用则淘汰
        self._clients_lock = threading.Lock()  # 保护 _user_clients 并发访问

        self._build_default_client()

    def _build_default_client(self):
        """根据当前配置构建默认 OpenAI 客户端"""
        self._client_key = (self.api_key, self.base_url)
        if self.api_key:
            try:
                from openai import OpenAI
                client_kwargs = {"api_key": self.api_key, "base_url": self.base_url}
                if httpx:
                    client_kwargs["timeout"] = httpx.Timeout(10.0, read=120.0)
                self.client = OpenAI(**client_kwargs)
                logger.info(f"OpenAI 客户端已初始化: {self.base_url}")
            except Exception as e:
                logger.error(f"OpenAI 客户端初始化失败: {e}")
        else:
            logger.warning("未配置 API 密钥，LLM 功能将不可用")

    def _sync_config(self) -> dict:
        """同步实例属性到当前 config，并返回配置原始快照（供调用方复用）。

        性能：原实现对 6 个属性各自调用一次 cfg.get_raw() 并重复做字典下钻，
        而本方法在每次请求（get_user_client）都会被调用；这里只取一次快照、下钻一次。
        """
        from data.config import get_config as _get_cfg
        raw = _get_cfg().get_raw()
        # 同步所有可能被热更新的属性
        for attr, path in (
            ("api_key", "llm.api_key"),
            ("base_url", "llm.base_url"),
            ("model", "llm.model"),
            ("max_tokens", "llm.max_tokens"),
            ("temperature", "llm.temperature"),
            ("max_context_rounds", "llm.max_context_rounds"),
        ):
            val = raw
            for k in path.split("."):
                val = val.get(k, {}) if isinstance(val, dict) else val
            if not isinstance(val, dict) and val != getattr(self, attr):
                setattr(self, attr, val)
        # 如果 api_key 或 base_url 变了，重建客户端
        if (self.api_key, self.base_url) != self._client_key:
            self._build_default_client()
        return raw

    def get_user_client(self, username: str):
        """获取用户的 LLM 客户端，如果用户有自定义 API 配置则使用用户的，否则使用默认的"""
        # 同步配置热更新：每次调用时检查配置是否变更，变更则重建客户端。
        # 复用 _sync_config() 返回的快照，避免再次读取配置。
        data = self._sync_config()
        if not username:
            return self.client, self.model
        try:
            api_config = None
            # 查找用户配置
            for item in data.get('web', {}).get('users') or []:
                if item.get('username') == username:
                    api_config = item.get('api_config')
                    break
            # 如果是管理员
            if api_config is None and username == 'admin':
                api_config = data.get('web', {}).get('admin', {}).get('api_config')
            if not api_config or not api_config.get('api_key'):
                return self.client, self.model
            # 创建用户专属客户端（线程安全）
            cache_key = f"{api_config['api_key']}:{api_config.get('base_url', '')}"
            now = time.time()
            with self._clients_lock:
                if cache_key in self._user_clients:
                    client, ts = self._user_clients[cache_key]
                    # TTL 检查：过期则删除重建
                    if now - ts > self._user_clients_ttl:
                        del self._user_clients[cache_key]
                        logger.debug(f"API 客户端缓存过期: {cache_key[:20]}...")
                    else:
                        # 移到末尾（最近使用），更新时间戳
                        self._user_clients.move_to_end(cache_key)
                        self._user_clients[cache_key] = (client, now)
                        user_model = api_config.get('model') or self.model
                        return client, user_model
                # 创建新客户端
                from openai import OpenAI
                user_client_kwargs = {
                    "api_key": api_config['api_key'],
                    "base_url": api_config.get('base_url') or self.base_url
                }
                if httpx:
                    user_client_kwargs["timeout"] = httpx.Timeout(10.0, read=120.0)
                new_client = OpenAI(**user_client_kwargs)
                self._user_clients[cache_key] = (new_client, now)
                logger.info(f"为用户 {username} 创建自定义 API 客户端")
                # LRU 淘汰最旧的缓存（同时清理过期条目）
                while len(self._user_clients) > self._user_clients_max:
                    evicted_key, (_, evicted_ts) = self._user_clients.popitem(last=False)
                    logger.debug(f"淘汰旧 API 客户端缓存: {evicted_key[:20]}...")
                user_model = api_config.get('model') or self.model
                return new_client, user_model
        except Exception as e:
            logger.debug(f"获取用户 API 配置失败: {e}")
            return self.client, self.model

    def _build_messages(self, system_prompt, history: List[dict], core_memory: Optional[str] = None,
                        message: Optional[str] = None) -> List[dict]:
        """构建完整 messages 列表。history 由调用方提供，本服务不持有状态。"""
        # 将时间信息合并到 system prompt 中，减少一条独立 system message 的开销
        now = datetime.now()
        wd = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        time_info = f"\n\n当前时间: {now.strftime('%Y-%m-%d %H:%M')} {wd[now.weekday()]}。用中文回答。"
        messages = [{"role": "system", "content": system_prompt + time_info}]
        if core_memory:
            messages.append({"role": "system", "content": f"## 核心记忆\n{core_memory}"})
        # 追加历史上下文（已由调用方截断到 max_context_rounds）
        messages.extend(history)
        # 追加当前用户消息
        if message:
            messages.append({"role": "user", "content": message})
        return messages

    def get_response(self, message: str, system_prompt: str,
                     history: Optional[List[dict]] = None,
                     core_memory: Optional[str] = None,
                     username: Optional[str] = None) -> LLMResult:
        """非流式调用。history 为调用方提供的对话历史（不含当前消息）。"""
        if not message.strip():
            return LLMResult(reply="你好呀~", usage=_empty_usage())

        # 获取用户专属客户端
        client, model = self.get_user_client(username)

        if not client:
            return LLMResult(
                reply="抱歉，AI 服务不可用。请在设置中配置 API 密钥。",
                usage=_empty_usage(),
                error="client_missing",
            )

        history = history or []
        messages = self._build_messages(system_prompt, history, core_memory, message)

        try:
            logger.info(f"[OpenAI] 请求: model={model} msgs={len(messages)} temp={self.temperature}")
            resp = client.chat.completions.create(
                model=model, messages=messages,
                temperature=self.temperature, max_tokens=self.max_tokens)
            reply = self._filter(resp.choices[0].message.content or "")
            usage = resp.usage
            u = {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            }
            logger.info(f"[OpenAI] 回复: len={len(reply)} tokens={u}")
            return LLMResult(reply=reply, usage=u)
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return LLMResult(reply="AI服务暂时不可用，请查看服务器日志", usage=_empty_usage(), error=str(e))

    def get_response_stream(self, message: str, system_prompt: str,
                            history: Optional[List[dict]] = None,
                            core_memory: Optional[str] = None,
                            username: Optional[str] = None):
        """流式调用。history 为调用方提供的对话历史（不含当前消息）。
        返回 generator 逐块 yield 文本。流结束后会 yield 一个 usage dict 作为最后一个值。"""
        if not message.strip():
            yield "你好呀~"
            yield _empty_usage()
            return

        # 获取用户专属客户端
        client, model = self.get_user_client(username)

        if not client:
            yield "抱歉，AI 服务不可用。请在设置中配置 API 密钥。"
            yield _empty_usage()
            return

        history = history or []
        messages = self._build_messages(system_prompt, history, core_memory, message)

        full = ""
        prompt_tokens = 0
        completion_tokens = 0
        # 流式 think 标签过滤：使用缓冲区 + 状态检测
        # 优化：用字符串查找代替每chunk正则，只在需要时才做替换
        _buf = ""
        _THINK_START = "<think>"
        _THINK_END = "</think>"
        _THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
        _TRIPLE_NL_RE = re.compile(r"\n{3,}")

        # 表情标签过滤：与非流式 _filter() 保持一致。
        # prompt_base.md 明确要求模型输出 [happy]/[sad] 等标签，若流式路径不过滤，
        # 这些标签会原样显示在聊天气泡里并被写进数据库。
        _load_emotion_consts()
        _MAX_TAG_LEN = max(len(t) for t in _EMOTION_TAGS) + 1 if _EMOTION_TAGS else 8

        def _partial_tag_len(s: str) -> int:
            """返回 s 尾部与 <think> 前缀重合的最大长度（防止标签被 chunk 切割后泄露）"""
            max_l = min(len(s), len(_THINK_START) - 1)
            for l in range(max_l, 0, -1):
                if s.endswith(_THINK_START[:l]):
                    return l
            return 0

        def _partial_emotion_len(s: str) -> int:
            """返回 s 尾部应扣留的未闭合表情标签长度（防止 [happy] 被 chunk 切割后泄露）。

            只有确实是已知表情标签的前缀时才扣留，避免正文里普通的 "[" 被无限卡住。
            """
            idx = s.rfind("[")
            if idx < 0:
                return 0
            if "]" in s[idx:]:
                return 0          # 已闭合，交给正则处理
            frag = s[idx:]
            if len(frag) > _MAX_TAG_LEN:
                return 0          # 太长，不可能是表情标签，直接放行
            inner = frag[1:]
            if any(t.startswith(inner) for t in _EMOTION_TAGS):
                return len(frag)
            return 0
        try:
            logger.info(f"[OpenAI] 流式请求: model={model} msgs={len(messages)}")
            stream = client.chat.completions.create(
                model=model, messages=messages,
                temperature=self.temperature, max_tokens=self.max_tokens, stream=True)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full += text
                    _buf += text
                    # 快速检测：只有缓冲区可能包含 think 标签时才做正则
                    if _THINK_START in _buf:
                        cleaned = _THINK_RE.sub("", _buf)
                        think_start = cleaned.rfind(_THINK_START)
                        if think_start >= 0:
                            safe = cleaned[:think_start]
                            _buf = cleaned[think_start:]
                        else:
                            safe = cleaned
                            _buf = ""
                    else:
                        safe = _buf
                        _buf = ""
                    # 尾部可能是被切割的 <think> 前缀（如 "<thi"），扣回缓冲区等下一个 chunk
                    hold = _partial_tag_len(safe)
                    if hold:
                        _buf = safe[-hold:] + _buf
                        safe = safe[:-hold]
                    # 尾部可能是被切割的表情标签（如 "[hap"），同样扣回缓冲区
                    ehold = _partial_emotion_len(safe)
                    if ehold:
                        _buf = safe[-ehold:] + _buf
                        safe = safe[:-ehold]
                    if safe:
                        # 去掉已知表情标签，与非流式 _filter() 行为保持一致
                        if "[" in safe:
                            safe = _EMOTION_RE.sub("", safe)
                        # 三连换行清理延迟到 _filter，减少逐chunk开销
                        if safe:
                            yield safe
                if hasattr(chunk, 'usage') and chunk.usage:
                    prompt_tokens = getattr(chunk.usage, 'prompt_tokens', 0) or prompt_tokens
                    completion_tokens = getattr(chunk.usage, 'completion_tokens', 0) or completion_tokens
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            yield "\n[错误: AI服务异常，请查看服务器日志]"
            yield _empty_usage()
            return
        # flush 剩余缓冲（可能包含未闭合的 think 前缀，直接删除）
        if _buf:
            _buf = _THINK_RE.sub("", _buf)
            _buf = re.sub(r"<think>.*", "", _buf, flags=re.DOTALL)  # 删除未闭合的 think
            _buf = _EMOTION_RE.sub("", _buf)                        # 删除残留表情标签
            ehold = _partial_emotion_len(_buf)                       # 流已结束，未闭合的标签片段直接丢弃
            if ehold:
                _buf = _buf[:-ehold]
            _buf = re.sub(r"\n{3,}", "\n\n", _buf).strip()
            if _buf:
                yield _buf
        # API 不返回 usage 时粗略估算
        if prompt_tokens == 0:
            prompt_tokens = sum(len(m.get('content', '')) // 2 for m in messages)
        if completion_tokens == 0:
            completion_tokens = len(full) // 2
        # 最终清理 full（用于日志和数据库）
        full = self._filter(full)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        logger.info(f"[OpenAI] 流式完成: len={len(full)} tokens={usage}")
        # yield usage 作为最后一个值，让调用方获取
        yield usage

    def _filter(self, content: str) -> str:
        """过滤 LLM 输出：移除 think 标签和表情标签，保留 [SPLIT] 等功能性标签"""
        _load_emotion_consts()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = _EMOTION_RE.sub("", content)
        content = re.sub(r"\n{3,}", "\n\n", content)
        return content.strip()
