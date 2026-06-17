"""记忆服务 - 短期记忆 + 核心记忆摘要，持久化到 JSON 文件"""
import os
import json
import logging
import threading
from datetime import datetime
# typing not needed

logger = logging.getLogger("motochat")


class MemoryService:
    def __init__(self, root_dir, llm_service, max_groups=15):
        self.root_dir = root_dir
        self.llm = llm_service
        self.max_groups = max_groups
        self._lock = threading.Lock()

    def _mem_dir(self, avatar, user_id):
        d = os.path.join(self.root_dir, "avatars", avatar, "memory", user_id)
        os.makedirs(d, exist_ok=True)
        return d

    def _short_path(self, avatar, user_id):
        return os.path.join(self._mem_dir(avatar, user_id), "short_memory.json")

    def _core_path(self, avatar, user_id):
        return os.path.join(self._mem_dir(avatar, user_id), "core_memory.json")

    def init_files(self, avatar, user_id):
        sp = self._short_path(avatar, user_id)
        if not os.path.exists(sp):
            with open(sp, "w", encoding="utf-8") as f:
                json.dump([], f)
        cp = self._core_path(avatar, user_id)
        if not os.path.exists(cp):
            with open(cp, "w", encoding="utf-8") as f:
                json.dump({"timestamp": self._now(), "content": ""}, f)

    def add_conversation(self, avatar, user_msg, bot_reply, user_id, is_system=False):
        sp = self._short_path(avatar, user_id)
        with self._lock:
            try:
                data = []
                if os.path.exists(sp):
                    with open(sp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                entry = {"user": user_msg, "bot": bot_reply, "time": self._now()}
                if is_system:
                    entry["system"] = True
                data.append(entry)
                data = data[-self.max_groups:]
                with open(sp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"保存对话失败: {e}")

    def get_recent_context(self, avatar, user_id):
        sp = self._short_path(avatar, user_id)
        if not os.path.exists(sp):
            return []
        with self._lock:
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ctx = []
                for entry in data[-self.max_groups:]:
                    ctx.append({"role": "user", "content": entry["user"]})
                    ctx.append({"role": "assistant", "content": entry["bot"]})
                return ctx
            except Exception as e:
                logger.error(f"读取短期记忆失败: {e}")
                return []

    def get_core_memory(self, avatar, user_id):
        cp = self._core_path(avatar, user_id)
        if not os.path.exists(cp):
            return ""
        try:
            with open(cp, "r", encoding="utf-8") as f:
                return json.load(f).get("content", "")
        except Exception as e:
            logger.debug(f"读取核心记忆失败: {e}")
            return ""

    def update_core_memory(self, avatar, user_id):
        cp = self._core_path(avatar, user_id)
        existing = self.get_core_memory(avatar, user_id)
        context = self.get_recent_context(avatar, user_id)
        if not context:
            return False

        # 使用 memory.md 模板
        template_path = os.path.join(self.root_dir, "static", "prompts", "memory.md")
        base_prompt = ""
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                base_prompt = f.read()
        except Exception as e:
            logger.debug(f"读取记忆模板失败: {e}")

        prompt = base_prompt + "\n\n"
        prompt += f"现有核心记忆: {existing}\n\n最近对话:\n"
        for i in range(0, len(context), 2):
            u = context[i]["content"] if i < len(context) else ""
            b = context[i + 1]["content"] if i + 1 < len(context) else ""
            prompt += f"用户: {u}\n回复: {b}\n"

        try:
            result = self.llm.get_response(message=prompt, user_id=f"_core_mem_{user_id}", system_prompt="你是一个记忆管理助手。根据用户提供的对话记录，提取并更新核心记忆摘要。只输出更新后的核心记忆内容，不要输出其他内容。")
            if hasattr(result, "reply"):
                reply = result.reply
                error = result.error
            elif isinstance(result, tuple) and len(result) >= 2:
                reply, usage = result
                error = ""
            else:
                reply, error = str(result), ""
            if reply and "错误" not in reply and "Error" not in reply:
                with open(cp, "w", encoding="utf-8") as f:
                    json.dump({"timestamp": self._now(), "content": reply}, f, ensure_ascii=False, indent=2)
                self.llm.clear_context(f"_core_mem_{user_id}")
                logger.info(f"核心记忆已更新: {avatar}/{user_id}")
                return True
            else:
                logger.warning(f"核心记忆更新返回异常: reply={reply[:120]} error={error}")
        except Exception as e:
            logger.error(f"更新核心记忆失败: {e}")
        return False

    def clear_short_memory(self, avatar, user_id):
        with self._lock:
            with open(self._short_path(avatar, user_id), "w", encoding="utf-8") as f:
                json.dump([], f)

    def clear_core_memory(self, avatar, user_id):
        with self._lock:
            with open(self._core_path(avatar, user_id), "w", encoding="utf-8") as f:
                json.dump({"timestamp": self._now(), "content": ""}, f)

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
