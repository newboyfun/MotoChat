"""提醒服务 - 到期提醒 -> 写入通知表 + 广播"""
import logging
import threading
import time
import asyncio
import uuid
from datetime import datetime

logger = logging.getLogger("motochat")


class Reminder:
    def __init__(self, rid, cid, target, content):
        self.id = rid
        self.conversation_id = cid
        self.target_time = target
        self.content = content

    def is_due(self):
        return datetime.now() >= self.target_time


class ReminderService:
    def __init__(self, chat_service, broadcast_fn=None):
        self.chat = chat_service
        self.broadcast = broadcast_fn
        self._reminders = {}
        self._lock = threading.Lock()
        self._loop = None
        self._start()
        logger.info("提醒服务已启动")

    def set_event_loop(self, loop):
        self._loop = loop

    def _start(self):
        t = threading.Thread(target=self._loop_worker, daemon=True)
        t.start()

    def _loop_worker(self):
        while True:
            due = []
            with self._lock:
                for r in list(self._reminders.values()):
                    if r.is_due():
                        due.append(r)
                for r in due:
                    del self._reminders[r.id]
            for r in due:
                self._fire(r)
            time.sleep(1)

    def _fire(self, r):
        logger.info(f"触发提醒: {r.id}")
        try:
            prompt = (
                f"现在提醒时间到了，用户设定的提示内容为: {r.content}。"
                f"请以你的人设身份主动找用户聊天。"
            )
            reply = self.chat.handle(prompt, r.conversation_id, user="System", save_to_db=False)
            if isinstance(reply, tuple):
                reply = reply[0] if reply else ""
            reply = str(reply) if reply else "(无回复)"
            from app.database import save_notification

            save_notification(r.conversation_id, "提醒", reply)
            if self.broadcast:
                try:
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self.broadcast({"type": "notification", "title": "提醒", "content": reply}),
                            self._loop,
                        )
                    else:
                        logger.debug("提醒广播未执行：事件循环未就绪")
                except Exception as broadcast_err:
                    logger.warning(f"提醒广播失败: {broadcast_err}")
        except Exception as e:
            logger.error(f"提醒执行失败: {e}")

    def add(self, cid, target, content):
        rid = f"rem_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._reminders[rid] = Reminder(rid, cid, target, content)
        return rid

    def cancel(self, rid):
        with self._lock:
            return self._reminders.pop(rid, None) is not None

    def list_all(self):
        with self._lock:
            return [
                {
                    "id": r.id,
                    "conversation_id": r.conversation_id,
                    "target_time": r.target_time.isoformat(),
                    "content": r.content,
                }
                for r in self._reminders.values()
            ]
