"""记忆服务 - 短期记忆 + 核心记忆摘要，持久化到 JSON 文件"""
import os
import json
import logging
import threading
import time
from datetime import datetime
from collections import OrderedDict, defaultdict

logger = logging.getLogger("motochat")

# 缓存大小限制（多角色×多用户场景需要更大容量）
_MAX_CACHE_SIZE = 500
_MAX_LOCKS = 250


class MemoryService:
    def __init__(self, root_dir, llm_service, max_groups=15, src_root=None):
        self.root_dir = root_dir
        self.src_root = src_root or root_dir
        self.llm = llm_service
        self.max_groups = max_groups
        # 使用 defaultdict 自动创建锁，避免手动管理
        self._locks = defaultdict(threading.Lock)
        self._locks_lock = threading.Lock()  # 保护 _locks 字典的大小限制
        self._active_locks = {}  # {key: refcount} 跟踪正在使用的锁引用计数
        self._cache = OrderedDict()  # {(avatar, user_id, type): (mtime, data)} LRU 缓存
        self._cache_lock = threading.Lock()  # 保护 _cache 并发访问

    def _mem_dir(self, avatar, user_id):
        d = os.path.join(self.root_dir, "avatars", avatar, "memory", user_id)
        os.makedirs(d, exist_ok=True)
        return d

    def _short_path(self, avatar, user_id):
        return os.path.join(self._mem_dir(avatar, user_id), "short_memory.json")

    def _core_path(self, avatar, user_id):
        return os.path.join(self._mem_dir(avatar, user_id), "core_memory.json")

    def _get_lock(self, avatar, user_id):
        """获取 per-user 锁，避免全局锁阻塞。使用 refcount 跟踪活跃锁，清理时只删除 refcount=0 的锁。"""
        key = f"{avatar}:{user_id}"
        with self._locks_lock:
            lock = self._locks[key]  # defaultdict 自动创建
            # 引用计数 +1
            self._active_locks[key] = self._active_locks.get(key, 0) + 1
            # 定期清理未使用的锁（当锁数量超过上限时）
            if len(self._locks) >= _MAX_LOCKS:
                inactive_keys = [k for k in self._locks if self._active_locks.get(k, 0) == 0]
                keys_to_remove = inactive_keys[:_MAX_LOCKS // 2]
                for k in keys_to_remove:
                    if self._active_locks.get(k, 0) == 0:
                        del self._locks[k]
                        self._active_locks.pop(k, None)
                if keys_to_remove:
                    logger.debug(f"清理未活跃的锁: {len(keys_to_remove)} 个")
        return lock

    def _release_lock(self, avatar, user_id):
        """释放锁的活跃状态（在锁使用完成后调用）"""
        key = f"{avatar}:{user_id}"
        with self._locks_lock:
            count = self._active_locks.get(key, 0)
            if count <= 1:
                self._active_locks.pop(key, None)
            else:
                self._active_locks[key] = count - 1

    def add_conversation(self, avatar, user_msg, bot_reply, user_id, is_system=False):
        sp = self._short_path(avatar, user_id)
        lock = self._get_lock(avatar, user_id)
        try:
            with lock:
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
                # 清除缓存（线程安全）
                cache_key = (avatar, user_id, "short")
                with self._cache_lock:
                    self._cache.pop(cache_key, None)
        except Exception as e:
            logger.error(f"保存对话失败: {e}")
        finally:
            self._release_lock(avatar, user_id)

    def get_recent_context(self, avatar, user_id):
        sp = self._short_path(avatar, user_id)
        if not os.path.exists(sp):
            return []
        # 检查缓存
        cache_key = (avatar, user_id, "short")
        try:
            mtime = os.path.getmtime(sp)
        except OSError:
            mtime = 0
        with self._cache_lock:
            if cache_key in self._cache:
                cached_mtime, cached_data = self._cache[cache_key]
                if cached_mtime == mtime:
                    # 移到末尾（最近使用）
                    self._cache.move_to_end(cache_key)
                    return cached_data
        lock = self._get_lock(avatar, user_id)
        try:
            with lock:
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ctx = []
                for entry in data[-self.max_groups:]:
                    ctx.append({"role": "user", "content": entry["user"]})
                    ctx.append({"role": "assistant", "content": entry["bot"]})
                # 更新缓存（LRU 淘汰）
                with self._cache_lock:
                    self._cache[cache_key] = (mtime, ctx)
                    self._cache.move_to_end(cache_key)
                    # 超过上限时淘汰最旧的
                    while len(self._cache) > _MAX_CACHE_SIZE:
                        self._cache.popitem(last=False)
                return ctx
        except Exception as e:
            logger.error(f"读取短期记忆失败: {e}")
            return []
        finally:
            self._release_lock(avatar, user_id)

    def get_core_memory(self, avatar, user_id):
        cp = self._core_path(avatar, user_id)
        if not os.path.exists(cp):
            return ""
        # 检查缓存
        cache_key = (avatar, user_id, "core")
        try:
            mtime = os.path.getmtime(cp)
        except OSError:
            mtime = 0
        with self._cache_lock:
            if cache_key in self._cache:
                cached_mtime, cached_data = self._cache[cache_key]
                if cached_mtime == mtime:
                    # 移到末尾（最近使用）
                    self._cache.move_to_end(cache_key)
                    return cached_data
        lock = self._get_lock(avatar, user_id)
        try:
            with lock:
                with open(cp, "r", encoding="utf-8") as f:
                    data = json.load(f).get("content", "")
                # 更新缓存（LRU 淘汰）
                with self._cache_lock:
                    self._cache[cache_key] = (mtime, data)
                    self._cache.move_to_end(cache_key)
                    # 超过上限时淘汰最旧的
                    while len(self._cache) > _MAX_CACHE_SIZE:
                        self._cache.popitem(last=False)
                return data
        except Exception as e:
            logger.debug(f"读取核心记忆失败: {e}")
            return ""
        finally:
            self._release_lock(avatar, user_id)

    def update_core_memory(self, avatar, user_id):
        cp = self._core_path(avatar, user_id)
        existing = self.get_core_memory(avatar, user_id)
        context = self.get_recent_context(avatar, user_id)
        if not context:
            return False

        # 使用 memory.md 模板
        template_path = os.path.join(self.src_root, "static", "prompts", "memory.md")
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
            result = self.llm.get_response(
                message=prompt,
                system_prompt="你是一个记忆管理助手。根据用户提供的对话记录，提取并更新核心记忆摘要。只输出更新后的核心记忆内容，不要输出其他内容。",
                history=[]
            )
            if hasattr(result, "reply"):
                reply = result.reply
                error = result.error
            elif isinstance(result, tuple) and len(result) >= 2:
                reply, usage = result[0], result[1]
                error = ""
            else:
                reply, error = str(result), ""
            if reply and not error:
                # 写入时加锁，防止与 clear_core_memory 等操作竞态
                cp = self._core_path(avatar, user_id)
                lock = self._get_lock(avatar, user_id)
                try:
                    with lock:
                        os.makedirs(os.path.dirname(cp), exist_ok=True)
                        with open(cp, "w", encoding="utf-8") as f:
                            json.dump({"timestamp": self._now(), "content": reply}, f, ensure_ascii=False, indent=2)
                        cache_key = (avatar, user_id, "core")
                        with self._cache_lock:
                            self._cache.pop(cache_key, None)
                finally:
                    self._release_lock(avatar, user_id)
                logger.info(f"核心记忆已更新: {avatar}/{user_id}")
                return True
            else:
                logger.warning(f"核心记忆更新返回异常: reply={reply[:120]} error={error}")
        except Exception as e:
            logger.error(f"更新核心记忆失败: {e}")
        return False

    def clear_core_memory(self, avatar, user_id):
        cp = self._core_path(avatar, user_id)
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        lock = self._get_lock(avatar, user_id)
        try:
            with lock:
                with open(cp, "w", encoding="utf-8") as f:
                    json.dump({"timestamp": self._now(), "content": ""}, f)
                # 清除缓存（线程安全）
                cache_key = (avatar, user_id, "core")
                with self._cache_lock:
                    self._cache.pop(cache_key, None)
        finally:
            self._release_lock(avatar, user_id)

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
