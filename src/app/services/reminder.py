"""提醒服务 - 到期提醒 -> 写入通知表 + 广播"""
import logging
import threading
import time
import asyncio
import uuid
import concurrent.futures
from datetime import datetime, timedelta

# 提前导入数据库模块，避免函数内重复导入
from app.database import load_all_reminders, delete_reminder, save_notification

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
        self._stop_event = threading.Event()  # 用于优雅停止
        self._wake_event = threading.Event()   # 用于唤醒工作线程检查新提醒
        # 共享线程池，避免每次触发提醒都创建新线程池
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="reminder")
        self._load_from_db()
        self._start()
        logger.info(f"提醒服务已启动 ({len(self._reminders)} 项已加载)")

    def stop(self):
        """优雅停止提醒服务"""
        self._stop_event.set()
        self._wake_event.set()  # 唤醒工作线程，使其能够检查停止标志
        # 关闭线程池，等待正在执行的任务完成
        if self._pool:
            self._pool.shutdown(wait=True, cancel_futures=True)
            logger.info("提醒服务线程池已关闭")

    def set_event_loop(self, loop):
        self._loop = loop

    def _load_from_db(self):
        try:
            rows = load_all_reminders()
            for row in rows:
                self._reminders[row['id']] = Reminder(
                    row['id'], row['conversation_id'], row['target_time'], row['content']
                )
        except Exception as e:
            logger.warning(f"加载提醒失败: {e}")

    def _start(self):
        t = threading.Thread(target=self._loop_worker, daemon=True)
        t.start()

    def _get_nearest_due_time(self):
        """获取最近的提醒时间"""
        with self._lock:
            if not self._reminders:
                return None
            nearest = min(r.target_time for r in self._reminders.values())
            return nearest

    def _loop_worker(self):
        while not self._stop_event.is_set():
            # 计算下次唤醒时间
            nearest = self._get_nearest_due_time()
            if nearest:
                wait_seconds = max(0.1, (nearest - datetime.now()).total_seconds())
                # 限制最大等待时间，避免时钟调整导致长时间休眠
                wait_seconds = min(wait_seconds, 60)
            else:
                wait_seconds = 30  # 无提醒时长休眠

            # 使用 _wake_event 等待，可被 stop() 或 add() 中断
            self._wake_event.wait(timeout=wait_seconds)
            self._wake_event.clear()  # 重置事件，准备下次等待

            if self._stop_event.is_set():
                break

            # 检查并触发到期提醒
            due = []
            with self._lock:
                for r in list(self._reminders.values()):
                    if r.is_due():
                        due.append(r)
                for r in due:
                    del self._reminders[r.id]
            for r in due:
                if self._fire(r):
                    try:
                        delete_reminder(r.id)
                    except Exception as e:
                        logger.warning(f"提醒删除失败: {e}")
                else:
                    # 触发失败（如 LLM 超时）：延后 60s 重新入队重试，DB 中保留，避免提醒永久丢失
                    r.target_time = datetime.now() + timedelta(seconds=60)
                    with self._lock:
                        self._reminders[r.id] = r

    def _fire(self, r) -> bool:
        """触发提醒。返回 True 表示已消费（可删除），False 表示需要重试"""
        logger.info(f"触发提醒: {r.id}")
        try:
            prompt = (
                f"现在提醒时间到了，用户设定的提示内容为: {r.content}。"
                f"请以你的人设身份主动找用户聊天。"
            )
            # 按会话归属解析用户名，让提醒使用该用户选择的人设（找不到则用 System 走全局角色）
            fire_user = "System"
            try:
                from data.config import get_config as _get_cfg
                for item in _get_cfg().get_raw().get("web", {}).get("users") or []:
                    if item.get("conversation_id") == r.conversation_id:
                        fire_user = item.get("username") or "System"
                        break
            except Exception as e:
                logger.debug(f"提醒解析用户失败: {e}")
            # 使用共享线程池 + 超时防止 LLM 调用长时间阻塞
            future = self._pool.submit(self.chat.handle, prompt, r.conversation_id, fire_user, None, False)
            try:
                reply = future.result(timeout=60)
            except Exception as timeout_err:
                logger.warning(f"提醒 {r.id} LLM 调用超时(60s)，稍后重试: {timeout_err}")
                return False
            if isinstance(reply, tuple):
                reply = reply[0] if reply else ""
            reply = str(reply) if reply else "(无回复)"
            save_notification(r.conversation_id, "提醒", reply)
            if self.broadcast:
                try:
                    # 等待 event loop 被设置（最多等待 10 秒）
                    loop = self._loop
                    if not loop:
                        for _ in range(20):  # 10 秒，每 0.5 秒检查一次
                            time.sleep(0.5)
                            loop = self._loop
                            if loop:
                                break
                    if loop:
                        # 按会话定向广播：只推给该提醒所属用户（+ 管理员），避免串台
                        asyncio.run_coroutine_threadsafe(
                            self.broadcast({"type": "notification", "title": "提醒", "content": reply},
                                           cid=r.conversation_id),
                            loop,
                        )
                    else:
                        logger.warning(f"提醒 {r.id} 广播未执行：事件循环未就绪（等待超时）")
                except Exception as broadcast_err:
                    logger.warning(f"提醒广播失败: {broadcast_err}")
            return True
        except Exception as e:
            logger.error(f"提醒执行失败: {e}")
            return True  # 非超时类异常不重试，避免死循环

    def add(self, cid, target, content):
        rid = f"rem_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._reminders[rid] = Reminder(rid, cid, target, content)
        try:
            from app.database import save_reminder
            save_reminder(rid, cid, target, content)
        except Exception as e:
            logger.warning(f"提醒持久化失败: {e}")
        # 唤醒工作线程，让它立即检查新提醒
        self._wake_event.set()
        return rid

    def cancel(self, rid):
        with self._lock:
            removed = self._reminders.pop(rid, None) is not None
        if removed:
            try:
                delete_reminder(rid)
            except Exception as e:
                logger.warning(f"提醒删除失败: {e}")
        return removed

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
