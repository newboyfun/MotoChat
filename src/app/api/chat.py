"""
聊天路由 — 聊天、流式、命令、TTS、图片识别、AI生成
从 server.py 拆分，使用 Depends 依赖注入
"""
import os
import json
import asyncio
import logging
import secrets
import threading
from datetime import datetime
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from app.core.deps import require_user, require_admin, get_chat_service, get_autosend_service
from app.core.utils import ROOT, sniff_image_ext
from app.core.streaming import ChunkCoalescer

logger = logging.getLogger("motochat")
router = APIRouter()

# 流式输出合并参数（与 WS 端点保持一致，见 api/ws.py）
_COALESCE_MIN = 24          # 字符数阈值：攒够这么多再发一次
_COALESCE_MAX_WAIT = 0.05   # 秒：距上次发送超过此值也立即发，保证低延迟


# ===== 请求模型 =====

class ChatReq(BaseModel):
    message: str = Field(max_length=10000)
    conversation_id: str = "default"

class CmdReq(BaseModel):
    command: str = Field(max_length=5000)
    conversation_id: str = "default"

class TTSReq(BaseModel):
    text: str = Field(max_length=5000)

class GeneratePersonalityReq(BaseModel):
    traits: list = []
    base_role: str = ""
    custom_hint: str = ""


# ===== 性格特征映射 =====

TRAIT_MAP = {
    "温柔": "gentle and tender personality",
    "活泼": "lively and energetic personality",
    "高冷": "cool and aloof personality",
    "幽默": "humorous and witty personality",
    "知性": "intellectual and knowledgeable personality",
    "元气": "cheerful and spirited personality",
    "毒舌": "sharp-tongued and sarcastic personality",
    "呆萌": "clumsy and adorable personality",
    "御姐": "mature and confident personality",
    "正经": "serious and proper personality",
    "话痨": "talkative and chatty personality",
    "安静": "quiet and reserved personality",
    "傲娇": "tsundere - cold outside but warm inside",
    "天然呆": "airheaded and naive personality",
    "腹黑": "scheming and mischievous personality",
    "中二": "chuunibyou - grandiose delusional personality",
    "社恐": "socially anxious and shy personality",
    "治愈": "healing and comforting personality",
}


# ===== 聊天接口 =====

@router.post("")
async def api_chat(req: ChatReq, user: dict = Depends(require_user)):
    _chat = get_chat_service()
    if not _chat:
        return JSONResponse(status_code=404, content={"error": "服务未就绪"})
    # 通知自动消息服务：用户有实际对话，重置空闲计时
    _autosend = get_autosend_service()
    if _autosend:
        _autosend.update_last_chat_time()
    cid = req.conversation_id
    uid = user.get("username")
    if user.get("role") == "user":
        cid = user.get("conversation_id") or cid
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _chat.handle(req.message, cid, user=uid))
    if isinstance(result, tuple):
        reply, usage = result
    else:
        reply = result
        usage = {}
    if isinstance(reply, str) and reply.startswith("抱歉，处理消息时出了点问题"):
        return JSONResponse(status_code=500, content={"error": reply, "reply": reply})
    return {"reply": reply, "usage": usage}


@router.post("/stream")
async def api_chat_stream(req: ChatReq, user: dict = Depends(require_user)):
    """SSE streaming chat endpoint"""
    _chat = get_chat_service()
    if not _chat:
        return JSONResponse(status_code=404, content={"error": "服务未就绪"})
    # 通知自动消息服务：用户有实际对话，重置空闲计时
    _autosend = get_autosend_service()
    if _autosend:
        _autosend.update_last_chat_time()
    cid = req.conversation_id
    uid = user.get("username")
    if user.get("role") == "user":
        cid = user.get("conversation_id") or cid
    # 限制队列大小，防止客户端断连时内存无限增长
    q = asyncio.Queue(maxsize=1000)
    _sentinel = object()
    stream_usage = {}
    loop = asyncio.get_running_loop()
    # 客户端断连时通知 producer 停止生成，避免线程池被无法取消的 LLM 流占满
    cancel_event = threading.Event()

    def _produce():
        """在线程池中执行的 producer，支持取消 + 背压 + 输出合并"""
        def _put(item):
            # None 表示「本次没有可放行的内容」（drain 空缓冲），直接跳过
            if item is None or cancel_event.is_set():
                return
            fut = asyncio.run_coroutine_threadsafe(q.put(item), loop)
            try:
                # 背压：队列满时阻塞 producer 而不是无限堆积 future
                fut.result(timeout=5)
            except Exception as e:
                logger.debug(f"SSE queue put failed: {e}")

        # 输出合并：LLM 逐 token 产出，若每个 token 都跨线程投递 + 发一个 SSE 事件，
        # 2000 字回复就是上千次 IPC + 上千帧（前端本就 50ms 批量渲染，纯浪费）。
        # 合并策略见 core/streaming.ChunkCoalescer（与 WS 端点共用同一实现）。
        coal = ChunkCoalescer(_COALESCE_MIN, _COALESCE_MAX_WAIT)

        try:
            for chunk in _chat.handle_stream(req.message, cid, user=uid):
                if cancel_event.is_set():
                    logger.info("[SSE] producer 收到取消信号，停止生成")
                    break
                # usage dict 是流的最后一个值，必须原样透传，不能并入文本
                if isinstance(chunk, dict):
                    _put(coal.drain())   # 先把已攒的正文发出去，保证顺序
                    _put(chunk)
                    continue
                _put(coal.add(chunk))
            _put(coal.drain())  # 收尾：把缓冲区里剩余文本发出去
        except Exception as e:
            _put(e)
        finally:
            # 确保 sentinel 一定会被放入队列，防止协程泄漏
            try:
                asyncio.run_coroutine_threadsafe(q.put(_sentinel), loop)
            except Exception as e:
                logger.debug(f"SSE sentinel put failed: {e}")

    # 直接在 run_in_executor 中执行 producer，避免两层异步嵌套
    task = loop.run_in_executor(None, _produce)

    async def _generate():
        chunk_count = 0
        try:
            while True:
                item = await q.get()
                if item is _sentinel:
                    break
                if isinstance(item, Exception):
                    logger.error(f"[SSE] 流式异常: {item}", exc_info=True)
                    yield f"event: error\ndata: {json.dumps({'error': 'stream_error', 'message': 'AI服务异常，请稍后重试'}, ensure_ascii=False)}\n\n"
                    break
                # 检查是否是 usage dict（来自 handle_stream）
                if isinstance(item, dict) and 'prompt_tokens' in item:
                    stream_usage['data'] = item
                else:
                    # ensure_ascii=False：中文不转义成 \uXXXX，SSE 帧体积约减半
                    # （与 WS 端 Starlette send_json 的默认行为保持一致）
                    data = json.dumps({"text": item}, ensure_ascii=False)
                    yield f"event: chunk\ndata: {data}\n\n"
                    chunk_count += 1
        except asyncio.CancelledError:
            # 客户端断连：直接退出，不发送 end 事件（连接已不存在）
            logger.info(f"[SSE] 流式被取消: chunks={chunk_count}")
            raise
        # end 事件放在正常流程里而不是 finally：
        # 客户端断连时外层会 aclose() 本生成器并抛入 GeneratorExit，
        # 若在 finally 中 yield 会触发 RuntimeError("async generator ignored GeneratorExit")。
        usage = stream_usage.get('data', {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        yield f"event: end\ndata: {json.dumps({'usage': usage, 'chunks': chunk_count}, ensure_ascii=False)}\n\n"
        logger.info(f"[SSE] 流式完成: chunks={chunk_count} tokens={usage}")

    async def _stream_with_cleanup():
        try:
            async for chunk in _generate():
                yield chunk
        except asyncio.CancelledError:
            logger.info("[SSE] 客户端断开，清理资源")
        finally:
            # 通知 producer 停止生成（防止 LLM 流无法取消占满线程池）
            cancel_event.set()
            # 确保队列中的等待协程能被唤醒
            try:
                while not q.empty():
                    q.get_nowait()
            except Exception as e:
                logger.debug(f"SSE queue drain failed: {e}")

    return StreamingResponse(
        _stream_with_cleanup(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/command")
async def api_command(req: CmdReq, user: dict = Depends(require_admin)):
    _chat = get_chat_service()
    if not _chat:
        return JSONResponse(status_code=503, content={"error": "聊天服务未就绪"})
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _chat.handle(req.command, req.conversation_id, user=user.get("username")))
    if isinstance(result, tuple):
        reply, usage = result
    else:
        reply = result
        usage = {}
    return {"reply": reply, "usage": usage}


@router.post("/tts")
async def api_tts(req: TTSReq, user: dict = Depends(require_user)):
    from app.core.deps import get_tts_service
    _tts = get_tts_service()
    if not _tts:
        return JSONResponse(status_code=500, content={"error": "TTS 服务未配置"})
    loop = asyncio.get_running_loop()
    try:
        path = await loop.run_in_executor(None, lambda: _tts.generate(req.text))
    except Exception as e:
        logger.error(f"TTS 生成失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "语音生成失败"})
    if not path:
        return JSONResponse(status_code=500, content={"error": "语音生成失败，请检查 TTS 配置"})
    return FileResponse(path, media_type="audio/mpeg", filename=os.path.basename(path))


@router.post("/images/recognize")
async def api_images_recognize(file: UploadFile = File(...), user: dict = Depends(require_user)):
    _chat = get_chat_service()
    if not _chat:
        return JSONResponse(status_code=503, content={"error": "服务未就绪"})
    if not hasattr(_chat, 'vision') or not _chat.vision:
        return JSONResponse(status_code=503, content={"error": "视觉服务未配置"})
    data = await file.read()
    if len(data) == 0:
        return JSONResponse(status_code=400, content={"error": "空文件"})
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"error": "图片大小超过 10MB 限制"})
    matched_ext = sniff_image_ext(data)
    if not matched_ext:
        return JSONResponse(status_code=415, content={"error": "仅支持 JPG/PNG/GIF/WebP 格式"})
    os.makedirs(os.path.join(os.path.dirname(ROOT), "userdata", "images", "temp"), exist_ok=True)
    path = os.path.join(os.path.dirname(ROOT), "userdata", "images", "temp", f"upload_{int(datetime.now().timestamp() * 1000)}_{secrets.token_hex(4)}.{matched_ext}")
    with open(path, "wb") as f:
        f.write(data)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _chat.vision.recognize(path))
    try:
        os.remove(path)
    except Exception as e:
        logger.debug(f"临时图片删除失败: {e}")
    if result is None:
        return JSONResponse(status_code=502, content={"error": "图片识别失败"})
    return {"recognized": result, "usage": {}}


@router.post("/generate-personality")
async def api_generate_personality(req: GeneratePersonalityReq, user: dict = Depends(require_user)):
    if not req.traits and not req.custom_hint:
        return JSONResponse(status_code=400, content={"error": "请至少选择一个性格特征或填写描述"})

    selected_desc = [TRAIT_MAP.get(t, t) for t in req.traits]
    selected_text = ", ".join(selected_desc)
    prompt_parts = ["你是一位AI角色设计师，请根据以下要求创建一个角色性格设定："]
    if selected_text:
        prompt_parts.append("性格特征：" + selected_text)
    if req.custom_hint:
        prompt_parts.append("额外要求：" + req.custom_hint)
    if req.base_role:
        prompt_parts.append("角色类型：" + req.base_role)
    prompt_parts.append("输出格式：\n1. 角色名字（2-3个字）\n2. 性格描述（2-3句话）\n3. 语气风格\n4. 行为禁忌\n\n直接输出角色设定，不超过200字。")
    gen_prompt = chr(10).join(prompt_parts)
    try:
        # 复用 LLMService 的客户端缓存，避免每次创建新连接
        _chat = get_chat_service()
        if not _chat or not _chat.llm:
            return JSONResponse(status_code=503, content={"error": "LLM 服务未就绪"})
        client, model = _chat.llm.get_user_client(user.get("username"))
        if not client:
            return JSONResponse(status_code=503, content={"error": "AI 服务不可用，请配置 API 密钥"})

        loop = asyncio.get_running_loop()
        def _call():
            return client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": gen_prompt}],
                temperature=0.8,
                # 推理模型（如 gemma4）会先消耗大量 token 做隐藏推理，
                # 预算太小会导致 content 为空，这里给足空间
                max_tokens=2000,
            )
        _resp = await loop.run_in_executor(None, _call)
        result = (_resp.choices[0].message.content or "").strip()
        if not result:
            return JSONResponse(status_code=500, content={"error": "模型返回为空，请重试"})
        return {"success": True, "content": result}
    except Exception as e:
        logger.error(f"操作失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "操作失败，请查看服务器日志"})
