"""自动消息服务，负责本地提醒/主动消息，通过广播推送"""
import logging
import random
import threading
from datetime import datetime

logger = logging.getLogger("motochat")


class AutoSendService:
    def __init__(self, config, chat_service, broadcast_fn=None):
        self.config = config
        self.chat = chat_service
        self.broadcast = broadcast_fn
        self._timer = None
        self._running = False
        self._last_chat = datetime.now()
        self._loop = None
        logger.info("自动消息服务已启动")

    def set_event_loop(self, loop):
        self._loop = loop

    def update_last_chat_time(self):
        self._last_chat = datetime.now()

    def start(self):
        self._running = True
        self._schedule_next()

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _is_quiet_time(self):
        try:
            now = datetime.now().time()
            start = datetime.strptime(self.config.behavior.quiet_time.start, "%H:%M").time()
            end = datetime.strptime(self.config.behavior.quiet_time.end, "%H:%M").time()
            if start <= end:
                return start <= now <= end
            else:
                return now >= start or now <= end
        except Exception:
            return False

    def _schedule_next(self):
        if not self._running:
            return
        min_s = int(self.config.behavior.auto_message.min_hours * 3600)
        max_s = int(self.config.behavior.auto_message.max_hours * 3600)
        delay = random.uniform(min_s, max_s)
        self._timer = threading.Timer(delay, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self):
        if not self._running:
            return
        if self._is_quiet_time():
            self._schedule_next()
            return
        content = self.config.behavior.auto_message.content
        try:
            if self.broadcast:
                if self._loop:
                    import asyncio

                    asyncio.run_coroutine_threadsafe(
                        self.broadcast({"type": "notification", "title": "主动消息", "content": content}),
                        self._loop,
                    )
                else:
                    logger.debug("自动消息广播未执行：事件循环未就绪")
            logger.info(f"自动消息已发送: {content}")
        except Exception as e:
            logger.error(f"自动消息失败: {e}")
        self._schedule_next()
