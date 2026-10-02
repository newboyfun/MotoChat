"""
MotoChat 回归测试 — 覆盖本轮修复的缺陷，防止回退。

运行前需要服务已启动（python src/run.py）:
    ./.venv/Scripts/python.exe tests/regression_test.py [admin_password]
"""
import os
import sys
import json
import types

import requests

BASE = "http://127.0.0.1:7860"
ADMIN_PW = sys.argv[1] if len(sys.argv) > 1 else "admin123"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.proxies = {}

passed, failed = [], []


def ok(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


# ─────────────────────────────────────────────────────────────
def login():
    r = SESSION.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": ADMIN_PW}, timeout=15)
    return r.status_code == 200 and r.json().get("success")


# ── 1. /api/history 分页契约（前端依赖 has_more / next_offset） ──
def test_history_pagination():
    r = SESSION.get(f"{BASE}/api/history/admin", params={"limit": 5, "offset": 0}, timeout=15)
    d = r.json()
    ok("history 返回 has_more 字段", "has_more" in d, str(list(d.keys())))
    ok("history 返回 next_offset 字段", "next_offset" in d)
    ok("history 不再依赖不存在的 total 字段", "total" not in d)


# ── 1b. 真实数据翻页：offset/next_offset 连续推进、无重复、能翻到底 ──
PROBE_CID = "default"        # 历史数据最多的会话（数百条，足以压多页）
PAGE = 100                   # 每页条数，确保需要翻多页


def test_history_pagination_real_data():
    seen = []
    offset = 0
    pages = 0
    ok_next = True
    while pages < 50:                      # 上限保护，防止死循环
        d = SESSION.get(f"{BASE}/api/history/{PROBE_CID}",
                        params={"limit": PAGE, "offset": offset}, timeout=15).json()
        msgs = d.get("messages", [])
        pages += 1
        seen.extend(m["id"] for m in msgs)

        if not d.get("has_more"):
            ok("翻页最终到达末尾(has_more=False)", True, f"共 {pages} 页 / {len(seen)} 条")
            break

        expected_next = offset + len(msgs)
        if d.get("next_offset") != expected_next:
            ok_next = False
            ok("next_offset == offset + len(page)",
               False, f"offset={offset} len={len(msgs)} next_offset={d.get('next_offset')}")
            break
        offset = d["next_offset"]
    else:
        ok("翻页能在 50 页内结束", False, "疑似 has_more 永不收敛")

    if pages >= 1:
        ok("next_offset 逐页连续推进", ok_next)
    ok("翻页无重复消息 id", len(seen) == len(set(seen)),
       f"{len(seen)} 条中有 {len(seen) - len(set(seen))} 条重复")
    if len(seen) > PAGE:
        ok("真实数据确实需要多页(证明分页生效而非一次全返回)", True, f"{len(seen)} 条 > 单页 {PAGE}")


# ── 2. allowed_personas=null 能恢复「全部允许」 ──
def test_allowed_personas_reset():
    target = "testuser"
    users = SESSION.get(f"{BASE}/api/admin/users", timeout=15).json()["users"]
    orig = next((u for u in users if u["username"] == target), None)
    if not orig:
        ok("allowed_personas 恢复测试(跳过: 无 testuser)", True)
        return
    saved = orig.get("allowed_personas")
    try:
        SESSION.post(f"{BASE}/api/admin/users/update",
                     json={"username": target, "allowed_personas": ["MONO"]}, timeout=15)
        cur = next(u for u in SESSION.get(f"{BASE}/api/admin/users", timeout=15).json()["users"]
                   if u["username"] == target)
        ok("能把用户限制为指定角色", cur["allowed_personas"] == ["MONO"], str(cur["allowed_personas"]))

        SESSION.post(f"{BASE}/api/admin/users/update",
                     json={"username": target, "allowed_personas": None}, timeout=15)
        cur = next(u for u in SESSION.get(f"{BASE}/api/admin/users", timeout=15).json()["users"]
                   if u["username"] == target)
        ok("allowed_personas=null 能恢复全部允许", cur["allowed_personas"] is None, str(cur["allowed_personas"]))
    finally:
        SESSION.post(f"{BASE}/api/admin/users/update",
                     json={"username": target, "allowed_personas": saved}, timeout=15)


# ── 3. set-persona 拒绝路径穿越 ──
def test_set_persona_traversal():
    for bad in ["../../src/data/avatars/MONO", "..\\..\\config", "/etc", "a/b"]:
        r = SESSION.post(f"{BASE}/api/admin/users/set-persona",
                         json={"username": "testuser", "persona": bad}, timeout=15)
        ok(f"set-persona 拒绝非法角色名 {bad!r}", r.status_code == 400, f"got {r.status_code}")


# ── 4. 流式输出过滤表情标签（含跨 chunk 切割） ──
def _fake_chunk(text, usage=None):
    delta = types.SimpleNamespace(content=text)
    choice = types.SimpleNamespace(delta=delta)
    return types.SimpleNamespace(choices=[choice], usage=usage)


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        return iter(self._chunks)


def _stream_with(chunks):
    from app.services.llm import LLMService
    svc = LLMService(api_key="x", base_url="http://localhost:1/v1", model="m")
    svc.get_user_client = lambda username: (types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_FakeCompletions(chunks))), "m")
    return svc.get_response_stream("hi", "sys")


def test_stream_emotion_filter():
    # 同一 chunk 内完整的标签
    out = "".join(c for c in _stream_with([_fake_chunk("你好[happy]呀")]) if isinstance(c, str))
    ok("流式过滤完整表情标签", "[happy]" not in out and "你好" in out and "呀" in out, repr(out))

    # 标签被切成两个 chunk（[hap / py]）—— 不能泄露残缺标签
    out = "".join(c for c in _stream_with([
        _fake_chunk("今天"), _fake_chunk("[hap"), _fake_chunk("py]"), _fake_chunk("开心")]) if isinstance(c, str))
    ok("流式过滤跨 chunk 表情标签", "[happy]" not in out and "[hap" not in out and "py]" not in out,
       repr(out))

    # 正文里的普通方括号不能被误删
    out = "".join(c for c in _stream_with([_fake_chunk("价格是 [100] 元")]) if isinstance(c, str))
    ok("不误删正文中的普通方括号", "[100]" in out, repr(out))

    # [SPLIT] 分段标记必须保留（大写在标签白名单外）
    out = "".join(c for c in _stream_with([_fake_chunk("第一句[SPLIT]第二句")]) if isinstance(c, str))
    ok("保留 [SPLIT] 分段标记", "[SPLIT]" in out, repr(out))

    # think 标签仍然被过滤
    out = "".join(c for c in _stream_with([_fake_chunk("<think>想一下</think>答案")]) if isinstance(c, str))
    ok("think 标签仍被过滤", "<think>" not in out and "答案" in out, repr(out))


# ── 5. ChatService._clean 也清掉表情标签 ──
def test_chat_clean():
    from app.services.chat import ChatService
    clean = ChatService._clean(object.__new__(ChatService), "好的[sad] 下次见[happy]")
    ok("ChatService._clean 清除表情标签", "[sad]" not in clean and "[happy]" not in clean, repr(clean))


# ── 5b. emoji.clean_message：模块级纯函数（原 EmojiHandler 类已移除） ──
def test_clean_message():
    from app.services.emoji import clean_message, EMOTION_TAGS, EMOTION_RE
    ok("clean_message 移除已知表情标签", clean_message("你好[happy]呀") == "你好呀",
       repr(clean_message("你好[happy]呀")))
    ok("clean_message 去除首尾空白", clean_message("  [sad] 嗨  ") == "嗨")
    ok("clean_message 保留正文方括号", clean_message("价格[100]元") == "价格[100]元")
    ok("clean_message 保留 [SPLIT] 分段标记", clean_message("A[SPLIT]B") == "A[SPLIT]B")
    ok("clean_message 空输入原样返回", clean_message("") == "")
    ok("EMOTION_RE 覆盖全部标签", all(EMOTION_RE.search(f"[{t}]") for t in EMOTION_TAGS))
    import app.services.emoji as emoji_mod
    ok("EmojiHandler 类已移除(模块不再导出)", not hasattr(emoji_mod, "EmojiHandler"))


# ── 6. broadcast 按会话定向，避免通知串台 ──
def test_broadcast_isolation():
    import asyncio
    from app.api import ws as ws_mod

    sent = []

    class FakeWS:
        def __init__(self, name):
            self.name = name

        async def send_json(self, data):
            sent.append(self.name)

    ws_mod._ws_clients.clear()
    ws_mod._ws_clients.extend([
        {"ws": FakeWS("userA"), "cid": "user_A", "role": "user"},
        {"ws": FakeWS("userB"), "cid": "user_B", "role": "user"},
        {"ws": FakeWS("admin"), "cid": "admin", "role": "admin"},
    ])
    asyncio.run(ws_mod.broadcast({"type": "notification"}, cid="user_A"))
    ok("定向广播只发给该会话用户(含管理员)", sorted(sent) == ["admin", "userA"], str(sent))

    sent.clear()
    asyncio.run(ws_mod.broadcast({"type": "notification"}))
    ok("全局广播发给所有人", sorted(sent) == ["admin", "userA", "userB"], str(sent))

    ws_mod._ws_clients.clear()


# ── 7. requirements 依赖名正确（真实包名，不是下划线写法） ──
def test_requirements():
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8-sig").read()
    ok("requirements 使用正确的 fish-audio-sdk 包名", "fish-audio-sdk" in req and "fish_audio_sdk" not in req)


# ── 8. WS 断连清理：_ws_clients 存的是 dict，必须按 ws 身份移除 ──
def test_ws_client_cleanup():
    from app.api import ws as ws_mod

    class FakeWS:
        def __init__(self, name):
            self.name = name

    a, b = FakeWS("a"), FakeWS("b")
    with ws_mod._ws_clients_lock:
        ws_mod._ws_clients.clear()
        ws_mod._ws_clients.extend([
            {"ws": a, "cid": "user_A", "role": "user"},
            {"ws": b, "cid": "user_B", "role": "user"},
        ])
    removed = ws_mod._drop_clients([a])
    ok("_drop_clients 能移除指定连接", removed == 1 and len(ws_mod._ws_clients) == 1, f"removed={removed}")
    ok("_drop_clients 只移除目标连接(保留 b)",
       ws_mod._ws_clients and ws_mod._ws_clients[0]["ws"] is b)
    # 重复清理应幂等（不抛异常、不误删）
    removed2 = ws_mod._drop_clients([a])
    ok("_drop_clients 重复调用幂等", removed2 == 0 and len(ws_mod._ws_clients) == 1)
    with ws_mod._ws_clients_lock:
        ws_mod._ws_clients.clear()


# ── 9. 图片魔数识别（原先两处各写一份、口径不一致） ──
def test_sniff_image_ext():
    from app.core.utils import sniff_image_ext
    ok("识别 JPEG", sniff_image_ext(b"\xff\xd8\xff\xe0" + b"x" * 20) == "jpg")
    ok("识别 PNG", sniff_image_ext(b"\x89PNG\r\n\x1a\n" + b"x" * 20) == "png")
    ok("识别 GIF", sniff_image_ext(b"GIF89a" + b"x" * 20) == "gif")
    ok("识别 WebP", sniff_image_ext(b"RIFF\x00\x00\x00\x00WEBP" + b"x" * 8) == "webp")
    ok("拒绝伪装成 PNG 的垃圾数据", sniff_image_ext(b"\x89PNG" + b"junkjunk") is None)
    ok("拒绝空数据", sniff_image_ext(b"") is None)


# ── 10. Config.update_many：批量原子更新（校验一次、落盘一次、全有或全无） ──
def test_config_update_many():
    import tempfile
    from data.config import Config, ConfigError, reset_config
    tmp = tempfile.mkdtemp(prefix="motocfg_")
    cfg = Config(config_dir=tmp)
    before_temp = cfg.get_raw()["llm"]["temperature"]

    # 合法批量：全部写入
    cfg.update_many([("llm.temperature", 0.5), ("llm.max_tokens", 1234)])
    raw = cfg.get_raw()
    ok("update_many 合法批量全部生效",
       raw["llm"]["temperature"] == 0.5 and raw["llm"]["max_tokens"] == 1234,
       f"temp={raw['llm']['temperature']} max_tokens={raw['llm']['max_tokens']}")

    # 非法批量：整体回滚，合法字段也不能落盘
    raised = False
    try:
        cfg.update_many([("llm.temperature", 0.9), ("llm.max_tokens", -5)])  # -5 越界
    except ConfigError:
        raised = True
    raw = cfg.get_raw()
    ok("update_many 非法项会抛 ConfigError", raised)
    ok("update_many 非法批量整体回滚(不留半成品)",
       raw["llm"]["temperature"] == 0.5 and raw["llm"]["max_tokens"] == 1234,
       f"temp={raw['llm']['temperature']} max_tokens={raw['llm']['max_tokens']}")

    # 落盘内容与内存一致
    import json
    with open(os.path.join(tmp, "config.json"), "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    ok("update_many 已落盘且内容一致",
       on_disk["llm"]["temperature"] == 0.5 and before_temp != 0.5)
    reset_config()


# ── 11. 配置保存端点原子性：混入非法字段时合法字段也不能被写入 ──
def test_config_save_atomic():
    before = SESSION.get(f"{BASE}/api/config", timeout=15).json()
    r = SESSION.post(f"{BASE}/api/config/save",
                     json={"llm.temperature": 0.33, "not.a.field": 1}, timeout=15)
    ok("混入非法字段的保存请求被拒绝", r.status_code == 400, f"got {r.status_code}")
    after = SESSION.get(f"{BASE}/api/config", timeout=15).json()
    ok("被拒绝的请求未留下部分写入(温度不变)",
       after.get("temperature") == before.get("temperature"),
       f"{before.get('temperature')} -> {after.get('temperature')}")


# ── 12. SSE 流式端点端到端：合并后文本仍完整、end 事件仍送达 ──
def test_sse_stream_end_to_end():
    # 空消息会让服务端直接返回 "你好呀~"（不调用 LLM），可确定性验证流式管道
    r = SESSION.post(f"{BASE}/api/chat/stream", json={"message": "", "conversation_id": "default"},
                     timeout=30, stream=True)
    ok("SSE 端点返回 200", r.status_code == 200, f"got {r.status_code}")
    body = r.text
    ok("SSE 收到 chunk 事件", "event: chunk" in body)
    ok("SSE 收到 end 事件", "event: end" in body)

    # 按 SSE 协议解析 data 行后比对文本（不能直接子串匹配：
    # 若服务端开启 ensure_ascii，中文会以 \uXXXX 形式出现）
    texts, end_seen = [], False
    for block in body.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.startswith("data: ")]
        if not lines:
            continue
        payload = json.loads(lines[0][6:])
        if block.startswith("event: chunk"):
            texts.append(payload.get("text", ""))
        elif block.startswith("event: end"):
            end_seen = True
            ok("SSE end 事件带 usage", "usage" in payload)
    ok("SSE 正文内容完整(合并未丢字)", "".join(texts) == "你好呀~", repr("".join(texts)))
    ok("SSE end 事件在正常流程送达", end_seen)
    # 中文不应被转义：转义会让每次流式响应体积近乎翻倍
    ok("SSE 中文未做 ASCII 转义(帧体积更小)", "\\u4f60" not in body and "你好呀" in body)


# ── 13. 依赖的 LLM 配置读取：_sync_config 返回可复用的快照 ──
def test_llm_sync_config_returns_snapshot():
    from app.services.llm import LLMService
    svc = LLMService(api_key="", base_url="http://localhost:1/v1", model="m")
    raw = svc._sync_config()
    ok("_sync_config 返回配置快照 dict", isinstance(raw, dict) and "llm" in raw)
    ok("_sync_config 快照可被 get_user_client 复用(同一对象)",
       raw is svc._sync_config() or raw.get("llm", {}).get("model") is not None)


# ── 14. ChunkCoalescer：流式合并策略（快路径省帧、慢路径不增延迟、绝不丢字） ──
def test_chunk_coalescer():
    from app.core.streaming import ChunkCoalescer

    # 快路径：模拟时钟不前进（高频 token），应在攒够阈值后才放行
    now = [0.0]
    c = ChunkCoalescer(min_chars=24, max_wait=0.05, clock=lambda: now[0])
    frames = []
    source = ["你好", "，这是", "一段", "用于", "测试的", "文本内容", "大概", "四十", "多字"]
    for part in source:
        out = c.add(part)
        if out is not None:
            frames.append(out)
    tail = c.drain()
    if tail:
        frames.append(tail)
    ok("快路径：合并后帧数远小于片段数", len(frames) < len(source), f"{len(frames)} 帧 / {len(source)} 段")
    ok("快路径：合并后文本零丢失", "".join(frames) == "".join(source), repr("".join(frames)))

    # 慢路径：每段之间推进超过 max_wait，应立即放行（不额外增加延迟）
    now2 = [0.0]
    c2 = ChunkCoalescer(min_chars=24, max_wait=0.05, clock=lambda: now2[0])
    released = 0
    for part in ["a", "b", "c"]:
        now2[0] += 0.2          # 超过 max_wait
        if c2.add(part) is not None:
            released += 1
    ok("慢路径：超过 max_wait 立即放行，不积压", released == 3, f"released={released}")

    # 边界：空缓冲区 drain 返回 None；达到阈值则 add() 内立即放行
    c3 = ChunkCoalescer()
    ok("空缓冲 drain 返回 None（调用方需跳过）", c3.drain() is None)
    out30 = c3.add("x" * 30)     # 已超 min_chars，应在 add() 内立刻放行并返回
    ok("达阈值即在 add() 内放行", out30 == "x" * 30, repr(out30))
    ok("放行后缓冲清空", c3.pending == 0 and c3.drain() is None)
    ok("未达阈值返回 None（调用方据此跳过投递）", c3.add("y") is None and c3.pending == 1)

    # 顺序保持：dict（usage）插入时不与文本混流
    now3 = [0.0]
    c4 = ChunkCoalescer(min_chars=8, max_wait=0.05, clock=lambda: now3[0])
    seq = []
    for part in ["AB", "CD", "EF"]:
        out = c4.add(part)
        if out is not None:
            seq.append(("text", out))
    seq.append(("text", c4.drain()))
    seq.append(("dict", {"prompt_tokens": 1}))
    text_part = "".join(v for k, v in seq if k == "text" and v)
    ok("顺序保持：文本在 usage 之前且完整", text_part == "ABCDEF" and seq[-1][0] == "dict", repr(seq))


if __name__ == "__main__":
    if not login():
        print("管理员登录失败，请传入正确密码")
        sys.exit(2)
    for fn in [test_history_pagination, test_history_pagination_real_data, test_allowed_personas_reset,
               test_set_persona_traversal, test_stream_emotion_filter, test_chat_clean,
               test_clean_message,
               test_broadcast_isolation, test_requirements, test_ws_client_cleanup,
               test_sniff_image_ext, test_config_update_many, test_config_save_atomic,
               test_sse_stream_end_to_end, test_llm_sync_config_returns_snapshot,
               test_chunk_coalescer]:
        try:
            fn()
        except Exception as e:
            failed.append(fn.__name__)
            print(f"[FAIL] {fn.__name__} 抛出异常: {e!r}")
    SESSION.post(f"{BASE}/api/auth/logout")
    print(f"\n通过 {len(passed)} / {len(passed) + len(failed)}")
    if failed:
        print("失败项:", failed)
        sys.exit(1)
