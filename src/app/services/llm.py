"""
LLM 服务 - 对话核心
封装 OpenAI 兼容 API，支持上下文管理/备用模型切换/流式输出
"""
import re, logging
from datetime import datetime
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger("motochat")


@dataclass
class LLMResult:
    reply: str
    usage: Dict[str, int]
    error: str = ""


class LLMService:
    def __init__(self, api_key, base_url, model, max_tokens=2000,
                 temperature=1.1, max_context_rounds=15, auto_model_switch=False):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_context_rounds = max_context_rounds
        self.auto_model_switch = auto_model_switch
        self.client = None
        self._contexts = {}
        self._last_usage = self._empty_usage()

        if api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=api_key, base_url=base_url)
                logger.info("OpenAI 客户端已初始化")
            except Exception as e:
                logger.error(f"OpenAI 客户端初始化失败: {e}")
        else:
            logger.warning("未配置 API 密钥，LLM 功能将不可用")

    def _ctx(self, user_id):
        if user_id not in self._contexts:
            self._contexts[user_id] = []
        return self._contexts[user_id]

    def _trim(self, user_id):
        ctx = self._ctx(user_id)
        mx = self.max_context_rounds * 2
        if len(ctx) > mx:
            self._contexts[user_id] = ctx[-mx:]

    def _build_messages(self, system_prompt, user_id, core_memory=None):
        """Build the full messages list for API call."""
        messages = [{"role": "system", "content": system_prompt}]
        if core_memory:
            messages.append({"role": "system", "content": f"## 核心记忆\n{core_memory}"})
        now = datetime.now()
        wd = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]
        messages.append({"role": "system",
            "content": f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} {wd[now.weekday()]}。用中文回答。"})
        messages.extend(self._ctx(user_id))
        return messages

    def add_context(self, user_id, role, content):
        self._ctx(user_id).append({"role": role, "content": content})
        self._trim(user_id)

    def load_history(self, user_id, history):
        if user_id not in self._contexts:
            self._contexts[user_id] = history.copy()

    def clear_context(self, user_id):
        self._contexts.pop(user_id, None)

    def _empty_usage(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def get_response(self, message, user_id, system_prompt,
                     previous_context=None, core_memory=None) -> LLMResult:
        if not message.strip():
            return LLMResult(reply="你好呀~", usage=self._empty_usage())

        if not self.client:
            return LLMResult(
                reply="抱歉，AI 服务不可用。请在设置中配置 API 密钥。",
                usage=self._empty_usage(),
                error="client_missing",
            )

        if previous_context and user_id not in self._contexts:
            self._contexts[user_id] = previous_context.copy()
        self.add_context(user_id, "user", message)

        messages = self._build_messages(system_prompt, user_id, core_memory)

        try:
            logger.info(f"[OpenAI] 发送请求: model={self.model} messages={len(messages)}条 temperature={self.temperature} max_tokens={self.max_tokens}")
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages,
                temperature=self.temperature, max_tokens=self.max_tokens)
            reply = self._filter(resp.choices[0].message.content or "")
            self.add_context(user_id, "assistant", reply)
            usage = resp.usage
            logger.info(f"[OpenAI] 收到回复: len={len(reply)} prompt_tokens={usage.prompt_tokens if usage else 0} completion_tokens={usage.completion_tokens if usage else 0}")
            return LLMResult(
                reply=reply,
                usage={
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                },
            )
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return LLMResult(reply=f"抱歉，AI 服务暂时不可用: {e}", usage=self._empty_usage(), error=str(e))

    def get_response_stream(self, message, user_id, system_prompt, core_memory=None):
        if not message.strip():
            yield "你好呀~"; return
        if not self.client:
            yield "抱歉，AI 服务不可用。请在设置中配置 API 密钥。"; return
        self.add_context(user_id, "user", message)

        messages = self._build_messages(system_prompt, user_id, core_memory)

        full = ""
        prompt_tokens = 0
        completion_tokens = 0
        try:
            logger.info(f"[OpenAI] 流式请求: model={self.model} messages={len(messages)}条")
            stream = self.client.chat.completions.create(
                model=self.model, messages=messages,
                temperature=self.temperature, max_tokens=self.max_tokens, stream=True)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full += text
                    yield text
                if hasattr(chunk, 'usage') and chunk.usage:
                    prompt_tokens = getattr(chunk.usage, 'prompt_tokens', 0) or prompt_tokens
                    completion_tokens = getattr(chunk.usage, 'completion_tokens', 0) or completion_tokens
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            yield f"\n[错误: {e}]"; return
        # Estimate tokens if API doesn't provide them
        if prompt_tokens == 0:
            prompt_tokens = sum(len(m.get('content',''))//2 for m in messages)
        if completion_tokens == 0:
            completion_tokens = len(full)//2
        full = self._filter(full)
        logger.info(f"[OpenAI] 流式完成: total_len={len(full)} prompt_tokens={prompt_tokens} completion_tokens={completion_tokens}")
        self._last_usage = {"prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens}
        self.add_context(user_id, "assistant", full)

    def get_last_usage(self):
        return getattr(self, '_last_usage', {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})

    def _filter(self, content):
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = re.sub(r"\n{3,}", "\n\n", content)
        return content.strip()

