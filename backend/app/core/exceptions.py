"""Agent 业务异常及其 HTTP 响应处理器。"""

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
