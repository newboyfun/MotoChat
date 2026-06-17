"""TTS 语音合成服务 - Fish Audio SDK"""
import os, re, logging
from datetime import datetime
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
        os.makedirs(voice_dir, exist_ok=True)

    def clean_text(self, text):
        if _emoji:
            text = _emoji.replace_emoji(text, replace="")
        text = text.replace("$", ",").replace("\n", ",").replace("\r", "")
        text = re.sub(r"\[.*?\]", "", text)
        return text.strip()

    def generate(self, text) -> Optional[str]:
        if not self.api_key or not self.model_id:
            logger.warning("TTS 未配置")
            return None
        try:
            from fish_audio_sdk import Session, TTSRequest
            clean = self.clean_text(text)
            if not clean: return None
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(self.voice_dir, f"voice_{ts}.mp3")
            with open(path, "wb") as f:
                for chunk in Session(self.api_key).tts(
                    TTSRequest(reference_id=self.model_id, text=clean)):
                    f.write(chunk)
            return path
        except Exception as e:
            logger.error(f"TTS 生成失败: {e}")
            return None

    def cleanup(self, path):
        try:
            if path and os.path.isfile(path): os.remove(path)
        except Exception as e:
            logger.debug(f"语音文件清理失败: {e}")

