"""TTS 语音合成服务 - Fish Audio SDK"""
import os, logging, uuid, threading
from typing import Optional
try:
    import emoji as _emoji
except ImportError:
    _emoji = None

logger = logging.getLogger("motochat")

class TTSService:
    def __init__(self, api_key, model_id, voice_dir, base_url=None):
        self.api_key = api_key
        self.model_id = model_id
        self.voice_dir = voice_dir
        self.base_url = base_url
        self._session = None       # 复用 Fish Audio Session，避免每次新建连接
        self._session_key = None   # 跟踪 session 对应的 api_key，配置变更时重建
        self._session_lock = threading.Lock()
        os.makedirs(voice_dir, exist_ok=True)

    def _get_session(self):
        """获取（或懒加载重建）Fish Audio Session，线程安全"""
        with self._session_lock:
            if self._session is None or self._session_key != self.api_key:
                from fish_audio_sdk import Session
                self._session = Session(self.api_key)
                self._session_key = self.api_key
            return self._session

    def clean_text(self, text):
        if _emoji:
            text = _emoji.replace_emoji(text, replace="")
        text = text.replace("$", ",").replace("\n", ",").replace("\r", "")
        # 移除 [SPLIT] 分段标记
        text = text.replace("[SPLIT]", ",")
        # 移除已知表情标签（引用 emoji.py 的统一列表）
        from app.services.emoji import EMOTION_RE
        text = EMOTION_RE.sub("", text)
        return text.strip()

    def generate(self, text) -> Optional[str]:
        if not self.api_key or not self.model_id:
            logger.warning("TTS 未配置")
            return None
        path = None
        try:
            from fish_audio_sdk import TTSRequest
            clean = self.clean_text(text)
            if not clean: return None
            # 使用 uuid4 保证全局唯一，避免时间戳碰撞
            path = os.path.join(self.voice_dir, f"voice_{uuid.uuid4().hex}.mp3")
            with open(path, "wb") as f:
                for chunk in self._get_session().tts(
                    TTSRequest(reference_id=self.model_id, text=clean)):
                    f.write(chunk)
            return path
        except Exception as e:
            logger.error(f"TTS 生成失败: {e}")
            # 清理可能已创建的不完整文件
            if path:
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                        logger.debug(f"已清理不完整 TTS 文件: {path}")
                except OSError:
                    pass
            return None

    def cleanup_old(self, max_age_hours=24):
        """清理超过指定时间的旧 TTS 音频文件，防止无限累积"""
        import time as _time
        try:
            now = _time.time()
            cutoff = now - (max_age_hours * 3600)
            count = 0
            for name in os.listdir(self.voice_dir):
                if not name.startswith("voice_") or not name.endswith(".mp3"):
                    continue
                fpath = os.path.join(self.voice_dir, name)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        count += 1
                except OSError:
                    pass
            if count:
                logger.info(f"已清理 {count} 个过期 TTS 文件")
        except Exception as e:
            logger.debug(f"TTS 文件清理失败: {e}")

