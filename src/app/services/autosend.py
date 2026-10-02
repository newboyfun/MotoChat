"""自动消息服务，负责本地提醒/主动消息，通过广播推送"""
import asyncio
import logging
import random
import threading
import concurrent.futures
from datetime import datetime
import time

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
        # 共享线程池，避免 LLM 调用阻塞计时器线程
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="autosend")
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
        # 关闭线程池：不等待正在执行的 LLM 调用（可能长达 120s），避免阻塞进程退出
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _is_quiet_time(self):
        try:
            now = datetime.now().time()
            start = datetime.strptime(self.config.behavior.quiet_time.start, "%H:%M").time()
            end = datetime.strptime(self.config.behavior.quiet_time.end, "%H:%M").time()
            if start <= end:
                return start <= now <= end
            else:
                return now >= start or now <= end
        except Exception as e:
            logger.debug(f"Quiet time check failed: {e}")
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
        # 距离上次对话够久才发送
        min_idle = self.config.behavior.auto_message.min_hours * 3600
        if (datetime.now() - self._last_chat).total_seconds() < min_idle - 1:
            self._schedule_next()
            return
        content = self.config.behavior.auto_message.content
        # 使用线程池异步执行 LLM 调用，避免阻塞计时器线程
        future = self._pool.submit(self._send_message, content)
        # 添加完成回调，在消息发送完成后再调度下一次
        future.add_done_callback(self._on_message_sent)

    def _get_target_conversations(self):
        """获取所有用户的 conversation_id 列表，用于发送自动消息通知"""
        try:
            from data.config import get_config as _get_cfg
            data = _get_cfg().get_raw()
            cids = []
            for item in data.get("web", {}).get("users") or []:
                cid = item.get("conversation_id")
                if cid:
                    cids.append(cid)
            return cids if cids else ["default"]
        except Exception as e:
            logger.debug(f"Get target conversations failed: {e}")
            return ["default"]

    def _send_message(self, content):
        """在线程池中执行的消息发送"""
        try:
            prompt = (
                f"现在是一位AI角色主动联系用户的时候。提示主题: {content}。"
                f"请以你的人设身份，用自然亲切的语气发起对话。不要超过两句话。"
            )
            reply_tuple = self.chat.handle(prompt, "default", user="System", save_to_db=False)
            ai_msg = reply_tuple[0] if isinstance(reply_tuple, tuple) else str(reply_tuple)
            # 持久化到通知表：为每个用户的会话都保存一份通知
            target_cids = self._get_target_conversations()
            try:
                from app.database import save_notification
                for cid in target_cids:
                    save_notification(cid, "主动消息", ai_msg)
            except Exception as db_err:
                logger.debug(f"自动消息持久化失败: {db_err}")
            if self.broadcast:
                try:
                    loop = self._loop
                    if not loop:
                        for _ in range(20):
                            time.sleep(0.5)
                            loop = self._loop
                            if loop:
                                break
                    if loop:
                        asyncio.run_coroutine_threadsafe(
                            self.broadcast({"type": "notification", "title": "主动消息", "content": ai_msg}),
                            loop,
                        )
                    else:
                        logger.warning(f"自动消息广播未执行：事件循环未就绪（等待超时）")
                except Exception as broadcast_err:
                    logger.warning(f"自动消息广播失败: {broadcast_err}")
            # 发送成功后更新最后对话时间，避免短时间内重复发送
            self.update_last_chat_time()
            logger.info(f"自动消息已发送: {ai_msg[:60]}...")
            return ai_msg
        except Exception as e:
            logger.error(f"自动消息失败: {e}")
            return None

    def _on_message_sent(self, future):
        """消息发送完成的回调，然后调度下一次"""
        try:
            future.result()  # 获取结果，如果有异常会抛出
        except Exception as e:
            logger.error(f"自动消息执行异常: {e}")
        finally:
            # 消息发送完成后再调度下一次，避免短时间内重复发送
            if self._running:
                self._schedule_next()
