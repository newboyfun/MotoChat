"""
WebSocket 路由 — /ws/chat 实时双向聊天
从 server.py 拆出，包含连接管理 + 广播 + 流式取消机制
"""
import asyncio
import logging
import threading
import time
from typing import List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.deps import _auth_sessions, _auth_lock
from app.core.streaming import ChunkCoalescer

logger = logging.getLogger("motochat")
router = APIRouter()

# 流式输出合并参数（SSE 与 WS 保持一致）：攒够 ≥_COALESCE_MIN 字符，
# 或距上次投递 ≥_COALESCE_MAX_WAIT 秒，才向客户端发一次，减少跨线程投递与帧数。
_COALESCE_MIN = 24          # 字符数阈值
_COALESCE_MAX_WAIT = 0.05   # 秒；模型输出有停顿时，保证首字/尾字及时送达

# ===== 客户端连接管理 =====

# 每项: {"ws": WebSocket, "cid": 会话ID, "role": 角色}
# 记录 cid/role 是为了让通知能按会话定向投递，避免 A 用户的提醒弹到 B 用户界面上
_ws_clients: List[dict] = []
_ws_clients_lock = threading.Lock()


def _drop_clients(sockets) -> int:
    """按 WebSocket 对象身份从 _ws_clients 中移除对应条目，返回移除数量。

    _ws_clients 存的是 dict（含 ws/cid/role），不能用 `ws in _ws_clients`
    或 `list.remove(ws)` 判断——那是在拿 WebSocket 对象和 dict 比较，永远不成立，
    会导致断连客户端永久残留在列表里（内存泄漏 + 在线连接数失真）。
    """
    targets = set(id(s) for s in sockets)
    removed = 0
    with _ws_clients_lock:
        for i in range(len(_ws_clients) - 1, -1, -1):
            if id(_ws_clients[i].get("ws")) in targets:
                del _ws_clients[i]
                removed += 1
    return removed


def _client_count() -> int:
    with _ws_clients_lock:
        return len(_ws_clients)


async def broadcast(data: dict, cid: str = None):
    """向 WebSocket 客户端广播消息。

    cid 为 None 时广播给所有在线客户端（系统级主动消息）；
    指定 cid 时只发给该会话所属用户（+ 管理员），防止跨用户通知串台。
    """
    with _ws_clients_lock:
        clients_snapshot = list(_ws_clients)
    dead = []
    for item in clients_snapshot:
        if cid is not None and item.get("cid") != cid and item.get("role") != "admin":
            continue
        try:
            await item["ws"].send_json(data)
        except Exception as e:
            logger.debug(f"WebSocket send failed: {e}")
            dead.append(item["ws"])
    if dead:
        # 按 WebSocket 对象身份移除：list.remove() 走的是 == 比较，
        # 对 dict 条目既低效又可能误删，这里显式按身份匹配。
        _drop_clients(dead)


# ===== WebSocket 端点 =====

@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    # WebSocket 认证：只接受 HttpOnly Cookie（query token 会落 access_log 造成凭据泄露）
    token = (ws.cookies.get('kc_token') or '').strip()
    with _auth_lock:
        session = _auth_sessions.get(token)
        if not session or time.time() > session.get('expires_at', 0):
            user_info = None
        else:
            user_info = session.get('user')
    if not user_info:
        await ws.close(code=4001)
        return
    await ws.accept()
    _client_entry = {
        "ws": ws,
        "cid": user_info.get("conversation_id") or "",
        "role": user_info.get("role") or "user",
    }
    with _ws_clients_lock:
        _ws_clients.append(_client_entry)
    client_addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "unknown"
    logger.info(f"WebSocket 客户端已连接: {client_addr} 用户={user_info.get('username')}")

    from app.core.deps import get_chat_service, get_autosend_service
    _chat = get_chat_service()

    cancel_event = threading.Event()  # 在循环外初始化，确保 finally 中可访问
    _msg_times = []  # 消息级滑动窗口限流（HTTP 中间件不拦 WS 帧，这里单独限）
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
                continue
            # 长连接期间复查 session：登出/过期后立即断开
            with _auth_lock:
                s = _auth_sessions.get(token)
                expired = not s or time.time() > s.get('expires_at', 0)
            if expired:
                await ws.close(code=4001)
                return
            # 限流：与 SSE 聊天接口对齐（30 次/分钟）
            now = time.time()
            _msg_times[:] = [t for t in _msg_times if now - t < 60]
            if len(_msg_times) >= 30:
                await ws.send_json({"type": "error", "data": "发送过于频繁，请稍后再试"})
                continue
            _msg_times.append(now)
            if not _chat:
                await ws.send_json({"type": "error", "data": "服务未就绪"})
                continue
            content = data.get("message") or ""
            # 与 SSE 接口对齐：限制消息长度
            if len(content) > 10000:
                await ws.send_json({"type": "error", "data": "消息过长（最大 10000 字）"})
                continue
            cid = data.get("conversation_id") or "default"
            if user_info and user_info.get("role") == "user":
                cid = user_info.get("conversation_id") or cid
            # 通知自动消息服务：用户有实际对话，重置空闲计时（与 SSE 端点一致）
            _autosend = get_autosend_service()
            if _autosend:
                _autosend.update_last_chat_time()
            logger.info(f"[WS] 收到消息: 会话={cid} 长度={len(content)} 前50字={content[:50]}{'...' if len(content) > 50 else ''}")
            # 限制队列大小，防止客户端断连时内存无限增长
            q = asyncio.Queue(maxsize=1000)
            _sentinel = object()
            stream_usage = {}
            loop = asyncio.get_running_loop()
            # 客户端断连时通知 producer 停止生成
            cancel_event = threading.Event()

            def _produce():
                """在线程池中执行的 producer，支持取消 + 背压 + 输出合并"""
                def _put(item):
                    # None 表示「本次没有可放行的内容」（drain 空缓冲），直接跳过
                    if item is None or cancel_event.is_set():
                        return
                    fut = asyncio.run_coroutine_threadsafe(q.put(item), loop)
                    try:
                        fut.result(timeout=5)  # 背压：队列满时阻塞 producer
                    except Exception as e:
                        logger.debug(f"WS queue put failed: {e}")

                # 输出合并：LLM 逐 token 产出，若每个 token 都跨线程投递 + 发一帧，
                # 2000 字回复就要上千次 IPC。合并策略见 core/streaming.ChunkCoalescer
                # （与 SSE 端点共用同一实现，避免两处逻辑漂移）。
                coal = ChunkCoalescer(_COALESCE_MIN, _COALESCE_MAX_WAIT)

                try:
                    for chunk in _chat.handle_stream(content, cid, user=user_info.get("username")):
                        if cancel_event.is_set():
                            logger.info("[WS] producer 收到取消信号，停止生成")
                            break
                        # usage dict 是流的最后一个值，必须原样透传，不能并入文本
                        if isinstance(chunk, dict):
                            _put(coal.drain())  # 先把已攒的正文发出去，保证顺序
                            _put(chunk)
                            continue
                        _put(coal.add(chunk))
                    _put(coal.drain())  # 收尾：把缓冲区里剩余文本发出去
                except Exception as e:
                    _put(e)
                finally:
                    try:
                        asyncio.run_coroutine_threadsafe(q.put(_sentinel), loop)
                    except Exception as e:
                        logger.debug(f"WS sentinel put failed: {e}")

            # run_in_executor 直接返回 Future，不要再用 create_task 包装
            task = loop.run_in_executor(None, _produce)
            await ws.send_json({"type": "start"})
            chunk_count = 0
            try:
                while True:
                    item = await q.get()
                    if item is _sentinel:
                        break
                    if isinstance(item, Exception):
                        await ws.send_json({"type": "error", "data": str(item)})
                        break
                    # 检查是否是 usage dict（来自 handle_stream）
                    if isinstance(item, dict) and 'prompt_tokens' in item:
                        stream_usage['data'] = item
                    else:
                        await ws.send_json({"type": "chunk", "data": item})
                        chunk_count += 1
                usage = stream_usage.get('data', {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
                await ws.send_json({"type": "end", "usage": usage})
                logger.info(f"[WS] 流式完成: chunks={chunk_count} tokens={usage}")
            except Exception as e:
                cancel_event.set()  # 通知 producer 停止生成
                try:
                    await ws.send_json({"type": "error", "data": str(e)})
                except Exception as e2:
                    logger.debug(f"WebSocket消息处理失败: {e2}")
                logger.error(f"[WS] 处理失败: {e}", exc_info=True)
    except WebSocketDisconnect:
        logger.info(f"WebSocket 客户端已断开: {client_addr}")
    except Exception as e:
        logger.error(f"[WS] 连接异常: {e}")
    finally:
        # 通知 producer 停止生成，防止 LLM 流占满线程池
        cancel_event.set()
        _drop_clients([ws])
        logger.info(f"[WS] 连接已清理，当前连接数: {_client_count()}")
