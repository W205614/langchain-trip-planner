"""业务异常与全局异常处理器

设计:
- 路由层不再重复写 try/except, 业务出错直接 raise BizException
- 未知异常由 global_exception_handler 统一兜底, 记录完整堆栈并返回统一错误格式
- 统一错误结构 { success, code, message }:
    - code: 稳定错误码 (用于前端程序化判断, 而非解析人类可读的 message)
    - message: 人类可读描述
 安全:
    - 500 服务器内部错误不返回异常内部细节(路径/堆栈), 避免信息泄露
    - 完整堆栈只写入 error.log 供排查
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class BizException(Exception):
    """业务异常: 携带HTTP状态码 + 稳定错误码, 由全局异常处理器统一转响应"""

    def __init__(self, detail: str, status_code: int = 400, code: str = "BAD_REQUEST"):
        self.detail = detail
        self.status_code = status_code
        self.code = code
        super().__init__(detail)


def _error_body(code: str, message: str) -> dict:
    """统一的错误响应结构"""
    return {"success": False, "code": code, "message": message}


async def biz_exception_handler(request: Request, exc: BizException):
    """可控业务异常: 记警告日志 + 返回对应状态码"""
    logger.warning(
        f"业务异常 [{exc.status_code}] {exc.detail} (path={request.url.path}, "
        f"request_id={getattr(request.state, 'request_id', '?')})"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.detail),
    )


async def global_exception_handler(request: Request, exc: Exception):
    """未捕获异常: 记完整堆栈到 error 日志 + 返回 500 (不泄露内部细节)"""
    logger.exception(
        f"未捕获异常 (path={request.url.path}, "
        f"request_id={getattr(request.state, 'request_id', '?')}): {exc}"
    )
    return JSONResponse(
        status_code=500,
        content=_error_body("INTERNAL_ERROR", "服务器内部错误, 请稍后重试"),
    )
