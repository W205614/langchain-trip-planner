"""API 限流配置 (共享模块)

用 slowapi 按客户端 IP 限流, 主要保护耗资源的接口 (如 AI 行程生成)。
放独立模块避免从 main.py 导入造成循环依赖。
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局限流器: key=客户端IP, 默认不限制 (各接口通过 @limiter.limit 单独声明)
limiter = Limiter(key_func=get_remote_address, default_limits=[])
