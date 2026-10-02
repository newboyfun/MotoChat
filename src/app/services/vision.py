"""
图片识别服务 - OpenAI 兼容视觉 API
"""
import os, logging, base64
from pathlib import Path
from typing import Optional

# httpx 用于设置 OpenAI 客户端超时
try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger("motochat")

class VisionService:
    def __init__(self, api_key, base_url, model, temperature=0.7):
        self.model = model
        self.temperature = temperature
        self.client = None
        if api_key:
            try:
                from openai import OpenAI
                # 设置默认超时：连接10秒，读取60秒
                client_kwargs = {"api_key": api_key, "base_url": base_url}
                if httpx:
                    client_kwargs["timeout"] = httpx.Timeout(10.0, read=60.0)
                self.client = OpenAI(**client_kwargs)
                logger.info("Vision 客户端已初始化")
            except Exception as e:
                logger.error(f"Vision 初始化失败: {e}")
        else:
            logger.warning("未配置 Vision API 密钥")

    # 图片大小限制（与 API 层保持一致）
    MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
    # 压缩阈值：超过此大小时进行压缩
    COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2MB
    # 压缩后的最大尺寸
    MAX_DIMENSION = 1024

    def _compress_image(self, image_path: str) -> bytes:
        """压缩图片，返回压缩后的字节数据"""
        try:
            from PIL import Image
            import io
            with Image.open(image_path) as img:
                # 转换为 RGB（如果是 RGBA 或其他模式）
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                # 缩放（保持宽高比）
                if max(img.size) > self.MAX_DIMENSION:
                    img.thumbnail((self.MAX_DIMENSION, self.MAX_DIMENSION), Image.Resampling.LANCZOS)
                # 保存为 JPEG
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=85, optimize=True)
                return buffer.getvalue()
        except ImportError:
            logger.warning("PIL 未安装，跳过图片压缩")
            return None
        except Exception as e:
            logger.warning(f"图片压缩失败: {e}")
            return None

    def recognize(self, image_path) -> Optional[str]:
        if not self.client:
            return None
        try:
            # 检查文件大小，防止超大图片导致 OOM
            file_size = os.path.getsize(image_path)
            if file_size > self.MAX_IMAGE_SIZE:
                logger.warning(f"图片过大({file_size // 1024 // 1024}MB)，跳过识别")
                return "图片过大，无法识别（最大 10MB）"
            
            # 大图先压缩，减少内存占用和传输大小
            if file_size > self.COMPRESS_THRESHOLD:
                logger.info(f"图片较大({file_size // 1024 // 1024}MB)，尝试压缩")
                compressed = self._compress_image(image_path)
                if compressed:
                    data = base64.b64encode(compressed).decode()
                    mime = "image/jpeg"
                    logger.info(f"压缩完成: {len(compressed) // 1024}KB")
                else:
                    # 压缩失败，使用原图
                    with open(image_path, "rb") as f:
                        data = base64.b64encode(f.read()).decode()
                    ext = Path(image_path).suffix.lower()
                    mime = {".jpg":"image/jpeg",".jpeg":"image/jpeg",
                            ".png":"image/png",".gif":"image/gif",".webp":"image/webp"
                            }.get(ext, "image/jpeg")
            else:
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
                temperature=self.temperature, max_tokens=1000)
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"图片识别失败: {e}")
            return None
