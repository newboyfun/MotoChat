"""流式输出工具 — 把高频小片段合并成较少的批次。

背景：LLM 逐 token 产出，SSE / WebSocket 端点原先对每个 token 都做一次
`run_coroutine_threadsafe(q.put(...))` 跨线程投递并单独发一帧。一次 2000 字
回复会产生上千次 IPC 与上千帧，其中绝大多数对用户毫无意义（前端本就按
~50ms 批量渲染）。

本模块把「何时放行」这一策略抽出来，供 SSE 与 WS 两个端点共用并单独测试。
"""
import time
from typing import List, Optional


class ChunkCoalescer:
    """按「字符数阈值 + 时间阈值」合并流式片段。

    放行条件（满足其一）：
      - 缓冲区字符数 >= min_chars：快速流下大幅减少帧数与跨线程投递；
      - 距上次放行 >= max_wait 秒：慢速流下不额外增加延迟（最多多等 max_wait）。

    调用方遇到非文本项（如末尾的 usage dict）时，应先 drain() 再单独处理，
    以保证顺序不变。
    """

    __slots__ = ("_min_chars", "_max_wait", "_clock", "_buf", "_len", "_last")

    def __init__(self, min_chars: int = 24, max_wait: float = 0.05, clock=time.monotonic):
        self._min_chars = max(1, int(min_chars))
        self._max_wait = max(0.0, float(max_wait))
        self._clock = clock
        self._buf: List[str] = []
        self._len = 0
        self._last = clock()

    def add(self, text: str) -> Optional[str]:
        """加入一段文本；返回可放行的合并结果，None 表示继续攒着。"""
        if text:
            self._buf.append(text)
            self._len += len(text)
        if self._len and (self._len >= self._min_chars or (self._clock() - self._last) >= self._max_wait):
            return self.drain()
        return None

    def drain(self) -> Optional[str]:
        """强制放行缓冲区全部内容并重置计时；缓冲区为空返回 None。"""
        self._last = self._clock()
        if not self._len:
            return None
        out = "".join(self._buf)
        self._buf = []
        self._len = 0
        return out

    @property
    def pending(self) -> int:
        """当前缓冲区中待放行的字符数。"""
        return self._len
