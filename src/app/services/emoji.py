"""
表情标签处理 — 全项目唯一的表情标签定义来源 + 消息清理

prompt_base.md 要求模型输出 [happy]/[sad] 等标签，但聊天界面不展示这些标签，
所以每个入口（流式 LLM、非流式 LLM、前端渲染、TTS 朗读）都要把它们剥离。
EMOTION_TAGS / EMOTION_RE 是全项目唯一的标签定义，llm.py / chat.py / tts.py 都从这里引用。

历史说明：原先这里还有一个 EmojiHandler 类，但它唯一被用到的 clean_message()
是无状态纯函数，类、构造函数参数（root_dir/data_root）和另外两个方法
（extract_emotion_tags / get_emoji_for_emotion）都没有任何调用方，已移除。
"""
import re
import logging

logger = logging.getLogger("motochat")

# 统一的表情标签列表（所有模块应引用此列表，避免重复定义）
EMOTION_TAGS = (
    "happy", "sad", "angry", "neutral", "love", "funny", "cute", "bored", "shy",
    "embarrassed", "sleepy", "lonely", "hungry", "comfort", "surprise", "confused",
    "playful", "excited", "tease", "hot", "speechless", "scared",
    "afraid", "amused", "anxious", "confident", "cold", "suspicious",
    "loving", "curious", "envious", "jealous", "miserable", "sick",
    "ashamed", "indifferent", "sorry", "determined", "crazy", "bashful",
    "depressed", "enraged", "frightened", "interested", "hopeful",
    "regretful", "stubborn", "thirsty", "guilty", "nervous", "disgusted",
    "proud", "ecstatic", "frustrated", "hurt", "tired", "smug",
    "thoughtful", "optimistic", "relieved", "puzzled", "shocked",
    "joyful", "skeptical", "bad", "worried",
)

# 编译好的正则表达式，用于匹配表情标签。
# 靠「方括号 + 标签白名单」定界：正文里的其他方括号（如 [SPLIT]、[100]）不会被误删。
EMOTION_RE = re.compile(r"\[(" + "|".join(EMOTION_TAGS) + r")\]")


def clean_message(message: str) -> str:
    """移除消息中的已知表情标签，并去掉首尾空白（保留其他内容）"""
    if not message:
        return message
    return EMOTION_RE.sub("", message).strip()
