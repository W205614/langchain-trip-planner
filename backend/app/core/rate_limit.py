"""API 限流配置 (共享模块)

用 slowapi 按客户端 IP 限流, 主要保护耗资源的接口 (如 AI 行程生成)。
放独立模块避免从 main.py 导入造成循环依赖。
"""

from contextlib import contextmanager
from threading import BoundedSemaphore

from slowapi import Limiter
from slowapi.util import get_remote_address

from ..config import get_settings
from .exceptions import BizException

# 全局限流器: key=客户端IP, 默认不限制 (各接口通过 @limiter.limit 单独声明)
limiter = Limiter(key_func=get_remote_address, default_limits=[])


class LLMRequestGate:
    """限制单进程内正在执行的高成本 LLM 请求数。"""

    def __init__(self, max_concurrency: int):
        self._semaphore = BoundedSemaphore(max_concurrency)

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()

    @contextmanager
    def slot(self):
        if not self.try_acquire():
            raise BizException(
                "当前规划请求较多，请稍后重试",
                status_code=429,
                code="LLM_CONCURRENCY_LIMITED",
            )
        try:
            yield
        finally:
            self.release()


# 该保护是进程内的：Docker 单实例可直接生效；多副本部署仍需在网关或 Redis 层共享限流状态。
llm_request_gate = LLMRequestGate(get_settings().llm_request_max_concurrency)
