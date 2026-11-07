"""LLM服务模块 (LangChain ChatOpenAI)"""

import logging
import httpx
from .call_budget import hooks, async_hooks

from langchain_openai import ChatOpenAI
from ..config import get_settings

logger = logging.getLogger(__name__)

# 全局LLM实例
_llm_instance = None

# LLM调用自动重试次数。
# Each provider attempt must reserve its own budget; SDK retries are disabled.
_LLM_MAX_RETRIES = 0


def build_chat_model(*, model, api_key, base_url, timeout, max_tokens, kind,
                     reasoning_effort, temperature=None) -> ChatOpenAI:
    parameters = {"reasoning_effort": reasoning_effort} if model.startswith("gpt-6") else {"temperature": temperature}
    return ChatOpenAI(
        model=model,
        api_key=api_key or None,
        base_url=base_url or "https://api.openai.com/v1",
        use_responses_api=False,
        stream_usage=True,
        timeout=timeout,
        max_retries=_LLM_MAX_RETRIES,
        max_completion_tokens=max_tokens,
        http_client=httpx.Client(event_hooks=hooks(kind), follow_redirects=False),
        http_async_client=httpx.AsyncClient(event_hooks=async_hooks(kind), follow_redirects=False),
        **parameters,
    )


def _build_llm(timeout: int) -> ChatOpenAI:
    settings = get_settings()
    return build_chat_model(model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url, timeout=timeout, max_tokens=settings.llm_day_max_tokens,
        kind="text", reasoning_effort=settings.llm_reasoning_effort, temperature=settings.llm_temperature)


def get_llm(timeout: int | None = None) -> ChatOpenAI:
    """
    获取LLM实例(单例模式)

    使用 langchain-openai 的 ChatOpenAI 连接 OpenAI 官方 API。
    配置位于后端私有环境文件的 LLM_* 字段。

    Returns:
        ChatOpenAI实例
    """
    global _llm_instance
    settings = get_settings()
    effective_timeout = timeout or settings.llm_timeout

    # 单日调用需要独立硬超时，不能修改共享客户端影响其他请求。
    if timeout is not None and effective_timeout != settings.llm_timeout:
        return _build_llm(effective_timeout)

    if _llm_instance is None:
        _llm_instance = _build_llm(effective_timeout)

        logger.info(
            f"LLM服务初始化成功 | model={settings.llm_model}, "
            f"base_url={settings.llm_base_url or 'https://api.openai.com/v1 (官方默认)'}"
        )

    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None
