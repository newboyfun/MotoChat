""" 
核心聊天服务 - 整合 LLM + 记忆 + 图片识别 + 命令 + 表情
"""
import os, re, logging
from datetime import datetime

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

        # 表情处理器
        from app.services.emoji import EmojiHandler
        self.emoji = EmojiHandler(src_root)

        # 加载人设
        avatar_dir = os.path.join(data_root, "avatars", os.path.basename(config.behavior.avatar_dir))
        self.avatar_name = os.path.basename(avatar_dir)
        self.avatar_prompt = self._load_avatar(avatar_dir)
        self.avatar_names = self._extract_names(avatar_dir)
        self._avatar_dir = config.behavior.avatar_dir

        # 上下文轮数跟踪(用于表情频率控制)
        self._round_count = {}

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

    def handle(self, content, conversation_id="default", user="local_user", image_path=None, save_to_db=True):
        try:
            self._current_user = user
            if image_path and not content.strip():
                content = self.vision.recognize(image_path) or "用户发送了一张图片"
            if not content.strip():
                return "你好呀~", {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}
            if content.strip().startswith("/"):
                reply = self._exec_command(content.strip(), user)
                if save_to_db:
                    from app.database import save_message
                    save_message(conversation_id, "user", content, "command")
                    save_message(conversation_id, "assistant", reply, "text")
                return reply, {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}
            logger.info(f"[对话] 会话={conversation_id} 用户消息={content[:80]}...")
            return self._chat(content, user, conversation_id, save_to_db=save_to_db)
        except Exception as e:
            logger.error(f"处理消息失败: {e}", exc_info=True)
            return f"抱歉，处理消息时出错了: {e}", {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}

    def handle_stream(self, content, conversation_id="default", user="local_user", image_path=None):
        try:
            self._current_user = user
            if image_path and not content.strip():
                content = self.vision.recognize(image_path) or "用户发送了一张图片"
            if not content.strip():
                yield "你好呀~"
                return
            if content.strip().startswith("/"):
                result = self._exec_command(content.strip(), user)
                from app.database import save_message
                save_message(conversation_id, "user", content, "command")
                save_message(conversation_id, "assistant", result, "text")
                yield result
                return

            logger.info(f"[流式] 会话={conversation_id} 用户消息={content[:80]}...")
            sys_prompt = self._build_system_prompt()
            mem_user = self._mem_key(user)
            core_mem = self.memory.get_core_memory(self.avatar_name, mem_user)
            history = self.memory.get_recent_context(self.avatar_name, mem_user)
            self.llm.load_history(user, history)

            # 清理消息中的表情标签
            clean_msg = self.emoji.clean_message(content)

            full = ""
            for chunk in self.llm.get_response_stream(
                    clean_msg or content, user, sys_prompt, core_mem):
                full += chunk
                yield chunk

            # 流式结束后: 保存记忆和数据库
            full = self._clean(full)
            self.memory.add_conversation(self.avatar_name, content, full, mem_user)
            from app.database import save_message
            save_message(conversation_id, "user", content)
            save_message(conversation_id, "assistant", full)

            # Store stream usage for API to read
            self._last_stream_usage = self.llm.get_last_usage()
            logger.info(f"[流式] 完成: 回复长度={len(full)} tokens={self._last_stream_usage}")

            # 更新轮数
            self._round_count[user] = self._round_count.get(user, 0) + 1
        except Exception as e:
            logger.error(f"流式处理失败: {e}", exc_info=True)
            yield f"\n[错误: {e}]"

    # ==================== 对话核心 ====================

    def _chat(self, content, user, cid, save_to_db=True):
        sys_prompt = self._build_system_prompt()
        mem_user = self._mem_key(user)
        core_mem = self.memory.get_core_memory(self.avatar_name, mem_user)
        history = self.memory.get_recent_context(self.avatar_name, mem_user)
        self.llm.load_history(user, history)
        clean_msg = self.emoji.clean_message(content)
        result = self.llm.get_response(clean_msg or content, user, sys_prompt, core_memory=core_mem)
        if hasattr(result, "reply"):
            reply, usage = result.reply, result.usage
        else:
            reply, usage = (result[0], result[1]) if isinstance(result, tuple) and len(result) >= 2 else (str(result), {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})
        logger.info(f"[对话] 完成: 回复: len={len(reply)} tokens={usage}")
        reply = self._clean(reply)

        self.memory.add_conversation(self.avatar_name, content, reply, mem_user)
        if save_to_db:
            from app.database import save_message
            save_message(cid, "user", content)
            save_message(cid, "assistant", reply)

        self._round_count[user] = self._round_count.get(user, 0) + 1
        return reply, usage

    def _build_system_prompt(self):
        base_path = os.path.join(self.root_dir, "static", "prompt_base.md")
        base = self._load_file(base_path) or ""
        extra = ""
        try:
            from data.config import config as _cfg
            if getattr(self, "_current_user", None):
                _data = _cfg.get_raw().get("web", {})
                if self._current_user.startswith("user_"):
                    uname = self._current_user[5:]
                    for item in _data.get("users") or []:
                        if item.get("username") == uname:
                            override = item.get("persona_override", {}).get(self.avatar_name, "")
                            extra = override or item.get("persona_prompt", "")
                            break
                elif self._current_user == "local_user":
                    admin_cfg = _data.get("admin") or {}
                    admin_over = admin_cfg.get("admin_persona_override") or {}
                    extra = admin_over.get(self.avatar_name, "")
        except Exception as e:
            logger.debug(f"读取用户自定义角色设定失败: {e}")
        if extra:
            prompt = f"## 最高优先级角色设定\n{extra}\n\n## 基础角色设定\n{self.avatar_prompt}"
        else:
            prompt = self.avatar_prompt
        if base:
            return f"{base}\n\n{prompt}"
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
        core = self.memory.get_core_memory(self.avatar_name, user)
        return f"核心记忆:\n{core if core else '(空)'}"

    def _cmd_clear(self, user):
        self.memory.clear_core_memory(self.avatar_name, user)
        return "核心记忆已清空"

    def _cmd_gen_core_mem(self, user):
        success = self.memory.update_core_memory(self.avatar_name, user)
        if success:
            core = self.memory.get_core_memory(self.avatar_name, user)
            return f"核心记忆已更新:\n{core}"
        return "核心记忆更新失败(可能没有足够的对话记录)"

    # ==================== 内容生成 ====================

    def _generate_content(self, content_type, user):
        prompts_dir = os.path.join(self.root_dir, "static", "prompts")
        template = self._load_file(os.path.join(prompts_dir, f"{content_type}.md"))
        if not template:
            return f"找不到 /{content_type} 的提示模板"
        history = self.memory.get_recent_context(self.avatar_name, user)
        if history:
            recent = "\n".join(
                f"用户: {history[i]['content']}\n回复: {history[i+1]['content']}"
                for i in range(0, len(history)-1, 2)
            )
        else:
            recent = "(暂无对话记录)"
        now = datetime.now()
        wd = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]
        date_cn = f"{now.year}年{now.month}月{now.day}日 {wd[now.weekday()]}"
        prompt = template.replace("{avatar_name}", self.avatar_name).replace("{date_cn}", date_cn)
        system = (f"你是 {self.avatar_name}。根据角色设定和最近对话生成内容。不要使用表情符号。保持角色语气。\n\n"
                  f"{prompt}\n\n最近对话:\n{recent}")
        try:
            result = self.llm.get_response(
                f"请生成{content_type}", f"_gen_{content_type}_{user}", system)
            if hasattr(result, "reply"):
                resp = result.reply
            else:
                resp = result[0] if isinstance(result, tuple) else result
            self.llm.clear_context(f"_gen_{content_type}_{user}")
            return resp
        except Exception as e:
            return f"生成失败: {e}"

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
        except Exception:
            return None

    def _clean(self, text):
        if not text:
            return text
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def switch_avatar(self, avatar_dir):
        full = os.path.join(self.root_dir, avatar_dir)
        if os.path.exists(full):
            self.avatar_name = os.path.basename(full)
            self.avatar_prompt = self._load_avatar(full)
            self.avatar_names = self._extract_names(full)
            self._avatar_dir = avatar_dir
            logger.info(f"切换角色: {self.avatar_name}")