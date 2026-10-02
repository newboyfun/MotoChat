"""
公共工具函数 — 消除 server.py 中的重复代码
"""
import os
import re
import logging
from typing import Optional

logger = logging.getLogger("motochat")

# 项目路径
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "static")


def safe_persona_name(name: str) -> str:
    """防止路径遍历：允许字母数字下划线中划线及 CJK 字符"""
    if not name or len(name) > 32:
        return ""
    # 禁止路径遍历字符
    if any(c in name for c in ('/', '\\', '..', '\x00')):
        return ""
    # 只允许: 字母数字、下划线、中划线、中文/日文/韩文
    if not re.match(r"^[A-Za-z0-9_\-\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]+$", name):
        return ""
    return name


def mask_key(key: str) -> str:
    """掩码显示 API 密钥"""
    if not key:
        return "未配置"
    if len(key) >= 4:
        return "***" + key[-4:]
    return "***"


# 支持的图片格式文件头（魔数）。原先在 api/chat.py 与 api/personas.py 各写了一份，
# 两处判断口径还不一致（一处 PNG 校验完整 8 字节签名，一处只校验前 4 字节），统一到这里。
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg"),           # JPEG
    (b"\x89PNG\r\n\x1a\n", "png"),      # PNG
    (b"GIF8", "gif"),                   # GIF87a / GIF89a
)


def sniff_image_ext(data: bytes) -> Optional[str]:
    """按文件头识别图片真实类型，返回扩展名；无法识别返回 None。

    仅凭文件头判断，不信任客户端声明的文件名/扩展名。
    """
    if not data:
        return None
    for sig, ext in _IMAGE_SIGNATURES:
        if data.startswith(sig):
            return ext
    # WebP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def find_user_config(username: str) -> Optional[dict]:
    """查找用户配置"""
    from data.config import get_config as _get_cfg
    username = (username or '').strip()
    data = _get_cfg().get_raw()
    web = data.get('web', {})

    # 检查管理员
    admin = web.get('admin') or {}
    if admin.get('username') and admin.get('username') == username:
        return {
            'username': admin['username'],
            'password': admin.get('password', ''),
            'role': 'admin',
            'persona': None,
            'conversation_id': None,
            'admin_persona_override': admin.get('admin_persona_override', {}),
            'library_access': admin.get('library_access', True),
            'api_config': admin.get('api_config'),
        }

    # 检查普通用户
    for item in web.get('users') or []:
        if item.get('username') == username:
            return {
                'username': item['username'],
                'password': item.get('password', ''),
                'role': item.get('role', 'user'),
                'persona': item.get('persona') or 'MONO',
                'conversation_id': item.get('conversation_id') or f"user_{item['username']}",
                'library_access': item.get('library_access', False),
                'api_config': item.get('api_config'),
                'persona_override': item.get('persona_override', {}),
                'persona_prompt': item.get('persona_prompt', ''),
                'pending_persona_override': item.get('pending_persona_override', {}),
                'allowed_personas': item.get('allowed_personas'),
                'user_personas': item.get('user_personas') or {},
            }
    return None



def json_utf8_response(data):
    """返回 UTF-8 编码的 JSON 响应"""
    from fastapi.responses import JSONResponse
    return JSONResponse(content=data, headers={'Content-Type': 'application/json; charset=utf-8'})
