"""
表情处理服务 (Web版)
保留表情标签识别和表情包选择
"""
import os, re, random, logging
from typing import Optional

logger = logging.getLogger("motochat")

class EmojiHandler:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.emotion_types = [
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
        ]

    def extract_emotion_tags(self, text):
        tags = []
        start = 0
        while True:
            start = text.find("[", start)
            if start == -1: break
            end = text.find("]", start)
            if end == -1: break
            tag = text[start + 1:end].lower()
            if tag in self.emotion_types:
                tags.append(tag)
            start = end + 1
        return tags

    def get_emoji_for_emotion(self, emotion_type, avatar_dir):
        emoji_dir = os.path.join(self.root_dir, avatar_dir, "emojis")
        target_dir = os.path.join(emoji_dir, emotion_type)
        if not os.path.exists(target_dir): return None
        files = [f for f in os.listdir(target_dir) if f.lower().endswith((".gif",".jpg",".png",".jpeg"))]
        if not files: return None
        return os.path.join(target_dir, random.choice(files))

    def clean_message(self, message):
        return re.sub(r"\[[a-z0-9_]+\]", "", message).strip()
