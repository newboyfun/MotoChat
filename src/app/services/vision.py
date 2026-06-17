"""
图片识别服务 - OpenAI 兼容视觉 API
"""
import logging, base64
from pathlib import Path
from typing import Optional

logger = logging.getLogger("motochat")

class VisionService:
    def __init__(self, api_key, base_url, model, temperature=0.7):
        self.model = model
        self.temperature = temperature
        self.client = None
        if api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=api_key, base_url=base_url)
                logger.info("Vision 客户端已初始化")
            except Exception as e:
                logger.error(f"Vision 初始化失败: {e}")
        else:
            logger.warning("未配置 Vision API 密钥")

    def recognize(self, image_path) -> Optional[str]:
        if not self.client:
            return "图片识别服务不可用。请在设置中配置 API 密钥。"
        try:
            with open(image_path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            ext = Path(image_path).suffix.lower()
            mime = {".jpg":"image/jpeg",".jpeg":"image/jpeg",
                    ".png":"image/png",".gif":"image/gif",".webp":"image/webp"
                    }.get(ext, "image/jpeg")
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role":"user","content":[
                    {"type":"text","text":"请描述这张图片的内容。"},
                    {"type":"image_url","image_url":{"url":f"data:{mime};base64,{data}"}}]}],
                temperature=0.7, max_tokens=1000)
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"图片识别失败: {e}")
            return f"图片识别失败: {e}"
