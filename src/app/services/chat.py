"""
核心聊天服务 - 整合 LLM + 记忆 + 图片识别 + 命令 + 表情
"""
import os, re, logging, threading
from datetime import datetime

from app.services.emoji import clean_message

logger = logging.getLogger("motochat")

class ChatService:
    def __init__(self, src_root, data_root, config, llm, memory, vision, tts):
        self.root_dir = src_root
        self.data_root = data_root
        self.config = config
        self.llm = llm
        self.memory = memory
        self.vision = vision
        self.tts = tts

        # 加载人设
        avatar_dir = os.path.join(data_root, "avatars", os.path.basename(config.behavior.avatar_dir))
        self.avatar_name = os.path.basename(avatar_dir)
        self.avatar_prompt = self._load_avatar(avatar_dir)
        self.avatar_names = self._extract_names(avatar_dir)
        self._avatar_dir = config.behavior.avatar_dir

        # 缓存 prompt_base.md（运行时不会改变）
        self._base_prompt = self._load_file(os.path.join(src_root, "static", "prompt_base.md")) or ""

        # 角色切换锁：保护 avatar_name/avatar_prompt/avatar_names 的并发读写
        self._avatar_lock = threading.Lock()

        # 上下文轮数跟踪(用于表情频率控制)
        # _round_count 已移除：并发不安全且未被任何代码读取

        # 命令列表
        self._commands = {
            "help": lambda u: self._cmd_help(),
            "diary": lambda u: self._generate_content("diary", u),
            "state": lambda u: self._generate_content("state", u),
            "list": lambda u: self._generate_content("list", u),
            "clear": lambda u: self._cmd_clear(u),
            "mem": lambda u: self._cmd_mem(u),
            "gen_core_mem": lambda u: self._cmd_gen_core_mem(u),
        }
        logger.info(f"聊天服务就绪: 角色={self.avatar_name}, 名字={self.avatar_names}")

    # ==================== 核心入口 ====================

    def _mem_key(self, user):
        if user and user.startswith("user_"):
            return user
        return user or "local_user"

    def _resolve_avatar(self, user):
        """按用户解析生效人设，返回 (角色名, 角色提示词)。

        普通用户选择的 persona（config 里 web.users[].persona）优先；
        管理员/本地/系统调用沿用全局角色（/api/personas/switch 切换）。
        角色名同时作为记忆目录名，各用户各角色的记忆天然隔离。"""
        with self._avatar_lock:
            g_name, g_prompt = self.avatar_name, self.avatar_prompt
        if not user or user in ("admin", "local_user", "System"):
            return g_name, g_prompt
        try:
            from data.config import get_config as _get_cfg
            from app.core.utils import safe_persona_name
            for item in _get_cfg().get_raw().get("web", {}).get("users") or []:
                if item.get("username") != user:
                    continue
                sel = safe_persona_name(item.get("persona") or "")
                if not sel or sel == g_name:
                    break
                # 用户自建角色：内容存在 config 的 user_personas 里
                up = (item.get("user_personas") or {}).get(sel)
                if up:
                    return sel, up
                # 系统角色：从角色目录读 avatar.md
                path = os.path.join(self.data_root, "avatars", sel, "avatar.md")
                prompt = self._load_file(path)
                if prompt:
                    return sel, prompt
                logger.warning(f"用户 {user} 选择的角色 {sel} 不可用，回退全局角色 {g_name}")
                break
        except Exception as e:
            logger.debug(f"解析用户人设失败: {e}")
        return g_name, g_prompt

    def _resolve_image(self, content, image_path):
        """统一处理图片识别：有图片且无文字时调用 Vision，返回处理后的文本"""
        if image_path and not content.strip():
            recognized = self.vision.recognize(image_path)
            return recognized or "用户发送了一张图片"
        return content

    def handle(self, content, conversation_id="default", user="local_user", image_path=None, save_to_db=True):
        try:
            content = self._resolve_image(content, image_path)
            if not content.strip():
                return "你好呀~", {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}
            if content.strip().startswith("/"):
                reply = self._exec_command(content.strip(), user)
                # 只在 save_to_db=True 时写入记忆和数据库，避免提醒/自动消息污染长期记忆
                if save_to_db:
                    mem_user = self._mem_key(user)
                    self.memory.add_conversation(self._resolve_avatar(user)[0], content, reply, mem_user)
                    from app.database import save_message
                    save_message(conversation_id, "user", content, "command")
                    save_message(conversation_id, "assistant", reply, "text")
                return reply, {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}
            logger.info(f"[对话] 会话={conversation_id} 用户消息={content[:80]}...")
            return self._chat(content, user, conversation_id, save_to_db=save_to_db)
        except Exception as e:
            logger.error(f"处理消息失败: {e}", exc_info=True)
            return "抱歉，处理消息时出了点问题，请稍后再试~", {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}

    def handle_stream(self, content, conversation_id="default", user="local_user", image_path=None, save_to_db=True):
        try:
            content = self._resolve_image(content, image_path)
            if not content.strip():
                yield "你好呀~"
                yield {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                return
            if content.strip().startswith("/"):
                result = self._exec_command(content.strip(), user)
                # 只在 save_to_db=True 时写入记忆和数据库
                if save_to_db:
                    mem_user = self._mem_key(user)
                    self.memory.add_conversation(self._resolve_avatar(user)[0], content, result, mem_user)
                    from app.database import save_message
                    save_message(conversation_id, "user", content, "command")
                    save_message(conversation_id, "assistant", result, "text")
                yield result
                yield {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                return

            logger.info(f"[流式] 会话={conversation_id} 用户消息={content[:80]}...")
            # 按用户解析生效人设，后续读写记忆统一用同一个角色名，避免中途切角色导致错位
            avatar, avatar_prompt = self._resolve_avatar(user)
            sys_prompt = self._build_system_prompt(user, avatar, avatar_prompt)
            mem_user = self._mem_key(user)
            core_mem = self.memory.get_core_memory(avatar, mem_user)
            # 每次请求重新加载历史，不再依赖 LLM 内存态
            history = self.memory.get_recent_context(avatar, mem_user)

            # 清理消息中的表情标签
            clean_msg = clean_message(content)

            full = ""
            usage = None
            for chunk in self.llm.get_response_stream(
                    clean_msg or content, sys_prompt, history=history, core_memory=core_mem, username=user):
                # 检查是否是 usage dict（最后一个值）
                if isinstance(chunk, dict) and 'prompt_tokens' in chunk:
                    usage = chunk
                    yield chunk  # 透传给调用方（SSE/WS 端点）
                else:
                    full += chunk
                    yield chunk

            # 流式结束后: 保存记忆和数据库（只在 save_to_db=True 时写入）
            full = self._clean(full)
            if save_to_db:
                self.memory.add_conversation(avatar, content, full, mem_user)
                from app.database import save_message, save_token_usage
                save_message(conversation_id, "user", content)
                save_message(conversation_id, "assistant", full)
                # 保存 token 用量
                if usage and usage.get('total_tokens', 0) > 0:
                    save_token_usage(user, usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0), usage.get('total_tokens', 0))
        except Exception as e:
            logger.error(f"流式处理失败: {e}", exc_info=True)
            yield "\n[错误: 处理消息时出了点问题，请稍后再试~]"

    # ==================== 对话核心 ====================

    def _chat(self, content, user, cid, save_to_db=True):
        # 按用户解析生效人设（普通用户选择的 persona 优先）
        avatar, avatar_prompt = self._resolve_avatar(user)
        sys_prompt = self._build_system_prompt(user, avatar, avatar_prompt)
        mem_user = self._mem_key(user)
        core_mem = self.memory.get_core_memory(avatar, mem_user)
        # 每次请求重新加载历史，不再依赖 LLM 内存态
        history = self.memory.get_recent_context(avatar, mem_user)
        clean_msg = clean_message(content)
        result = self.llm.get_response(clean_msg or content, sys_prompt, history=history, core_memory=core_mem, username=user)
        if hasattr(result, "reply"):
            reply, usage = result.reply, result.usage
        else:
            reply, usage = (result[0], result[1]) if isinstance(result, tuple) and len(result) >= 2 else (str(result), {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})
        logger.info(f"[对话] 完成: 回复: len={len(reply)} tokens={usage}")
        reply = self._clean(reply)

        # 只在 save_to_db=True 时写入记忆和数据库，避免提醒/自动消息污染对话上下文
        if save_to_db:
            self.memory.add_conversation(avatar, content, reply, mem_user)
            from app.database import save_message, save_token_usage
            save_message(cid, "user", content)
            save_message(cid, "assistant", reply)
            # 保存 token 用量（与流式路径保持一致）
            if usage and usage.get('total_tokens', 0) > 0:
                save_token_usage(user, usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0), usage.get('total_tokens', 0))

        return reply, usage

    def _build_system_prompt(self, user=None, avatar_name=None, avatar_prompt=None):
        # 未传入已解析的人设时，回退到全局角色快照（加锁读取，确保并发安全）
        if avatar_name is None or avatar_prompt is None:
            with self._avatar_lock:
                avatar_name = self.avatar_name
                avatar_prompt = self.avatar_prompt
        current_avatar_name = avatar_name
        current_avatar_prompt = avatar_prompt
        extra = ""
        try:
            from data.config import get_config as _get_cfg
            if user:
                # 一次性快照化读取配置，避免中途修改导致的不一致
                _cfg = _get_cfg()
                _web_data = _cfg.get_raw().get("web", {})
                _users = _web_data.get("users") or []
                _admin_cfg = _web_data.get("admin") or {}
                _admin_over = _admin_cfg.get("admin_persona_override") or {}
                
                # 查找普通用户（直接匹配用户名）
                found = False
                for item in _users:
                    if item.get("username") == user:
                        _user_overrides = item.get("persona_override", {})
                        _user_prompt = item.get("persona_prompt", "")
                        override = _user_overrides.get(current_avatar_name, "")
                        extra = override or _user_prompt
                        found = True
                        break
                # 管理员（Web 登录 "admin" 或命令行 "local_user"）
                if not found and user in ("admin", "local_user"):
                    extra = _admin_over.get(current_avatar_name, "")
        except Exception as e:
            logger.debug(f"读取用户自定义角色设定失败: {e}")
        if extra:
            prompt = f"## 最高优先级角色设定\n{extra}\n\n## 基础角色设定\n{current_avatar_prompt}"
        else:
            prompt = current_avatar_prompt
        if self._base_prompt:
            return f"{self._base_prompt}\n\n{prompt}"
        return prompt

    def _exec_command(self, cmd, user):
        parts = cmd.split(maxsplit=1)
        name = parts[0][1:]
        logger.info(f"[指令] 执行: /{name} 会话用户={user}")
        fn = self._commands.get(name)
        if not fn:
            return f"未知命令: {name}。输入 /help 查看可用命令。"
        return fn(user)

    def _cmd_help(self):
        return ("可用命令:\n"
                "/help - 帮助\n/diary - 角色日记\n/state - 角色状态\n"
                "/list - 备忘录\n/mem - 查看记忆\n"
                "/clear - 清空核心记忆\n/gen_core_mem - 手动生成核心记忆")

    def _cmd_mem(self, user):
        core = self.memory.get_core_memory(self._resolve_avatar(user)[0], self._mem_key(user))
        return f"核心记忆:\n{core if core else '(空)'}"

    def _cmd_clear(self, user):
        self.memory.clear_core_memory(self._resolve_avatar(user)[0], self._mem_key(user))
        return "核心记忆已清空"

    def _cmd_gen_core_mem(self, user):
        avatar = self._resolve_avatar(user)[0]
        mem_user = self._mem_key(user)
        success = self.memory.update_core_memory(avatar, mem_user)
        if success:
            core = self.memory.get_core_memory(avatar, mem_user)
            return f"核心记忆已更新:\n{core}"
        return "核心记忆更新失败(可能没有足够的对话记录)"

    # ==================== 内容生成 ====================

    def _generate_content(self, content_type, user):
        prompts_dir = os.path.join(self.root_dir, "static", "prompts")
        template = self._load_file(os.path.join(prompts_dir, f"{content_type}.md"))
        if not template:
            return f"找不到 /{content_type} 的提示模板"
        avatar = self._resolve_avatar(user)[0]
        history = self.memory.get_recent_context(avatar, self._mem_key(user))
        if history:
            # 安全拼接：处理奇数长度（跳过无配对的最后一条）
            pairs = []
            for i in range(0, len(history) - 1, 2):
                if i + 1 < len(history):
                    pairs.append(f"用户: {history[i]['content']}\n回复: {history[i+1]['content']}")
            recent = "\n".join(pairs) if pairs else "(暂无对话记录)"
        else:
            recent = "(暂无对话记录)"
        now = datetime.now()
        wd = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]
        date_cn = f"{now.year}年{now.month}月{now.day}日 {wd[now.weekday()]}"
        prompt = template.replace("{avatar_name}", avatar).replace("{date_cn}", date_cn)
        system = (f"你是 {avatar}。根据角色设定和最近对话生成内容。不要使用表情符号。保持角色语气。\n\n"
                  f"{prompt}\n\n最近对话:\n{recent}")
        try:
            result = self.llm.get_response(
                f"请生成{content_type}", system,
                history=[])  # 生成内容用空 history，不污染对话上下文
            if hasattr(result, "reply"):
                resp = result.reply
            else:
                resp = result[0] if isinstance(result, tuple) else result
            return resp
        except Exception as e:
            logger.error(f"生成{content_type}失败: {e}", exc_info=True)
            return f"生成{content_type}失败，请稍后再试~"

    # ==================== 辅助 ====================

    def _load_avatar(self, avatar_dir):
        return self._load_file(os.path.join(avatar_dir, "avatar.md")) or "你是一个友好的AI助手。"

    def _extract_names(self, avatar_dir):
        content = self._load_avatar(avatar_dir)
        names = []
        for m in re.findall(r"你是([^，。！？\s]+)", content):
            if m not in names and len(m) <= 6 and "机器" not in m: names.append(m)
        for m in re.findall(r"名字[：:]\s*([^，。！？\s\n]+)", content):
            if m not in names and len(m) <= 6: names.append(m)
        return names if names else [self.avatar_name]

    def _load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.debug(f"File read failed: {path}: {e}")
            return None

    def _clean(self, text):
        if not text:
            return text
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        # 表情标签清理：与 LLMService._filter() 保持一致。
        # 流式路径的 chunk 已过滤，这里再兜一次，保证写进数据库/记忆的内容干净。
        from app.services.emoji import EMOTION_RE
        text = EMOTION_RE.sub('', text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def switch_avatar(self, avatar_dir):
        # avatar_dir 可能是完整路径或相对路径，统一处理
        if os.path.isabs(avatar_dir) or os.path.exists(avatar_dir):
            full = avatar_dir
        else:
            full = os.path.join(self.root_dir, avatar_dir)
        if os.path.exists(full):
            # 使用锁保护多属性原子更新，避免并发请求读到不一致状态
            with self._avatar_lock:
                self.avatar_name = os.path.basename(full)
                self.avatar_prompt = self._load_avatar(full)
                self.avatar_names = self._extract_names(full)
                self._avatar_dir = avatar_dir
            logger.info(f"切换角色: {self.avatar_name}")
            return True
        logger.warning(f"切换角色失败，目录不存在: {full}")
        return False

    def update_avatar_prompt(self, content: str, avatar_dir: str = None):
        """更新当前角色的提示词（线程安全）

        Args:
            content: 新的提示词内容
            avatar_dir: 可选，同时更新 avatar_names（传入角色目录路径）
        """
        with self._avatar_lock:
            self.avatar_prompt = content
            if avatar_dir:
                self.avatar_names = self._extract_names(avatar_dir)
            logger.info(f"角色提示词已更新: {self.avatar_name}")