"""Agent 业务异常响应与默认路由边界。"""

import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI
from app.core.exceptions import biz_exception_handler
from app.core.exceptions import BizException
from app.services.call_budget import CallBudgetExceeded


@pytest.fixture
def no_raise_client():
    """只注册实际 Agent 使用的业务异常处理器"""
    app = FastAPI()
    app.add_exception_handler(BizException, biz_exception_handler)
    return TestClient(app, raise_server_exceptions=False)


def test_biz_exception_returns_status_code(no_raise_client):
    """BizException 应返回对应状态码与统一错误格式"""

    @no_raise_client.app.get("/test-biz")
    def test_biz():
        raise BizException("景点不存在", status_code=404)

    resp = no_raise_client.get("/test-biz")

    assert resp.status_code == 404
    data = resp.json()
    assert data["success"] is False
    assert data["message"] == "景点不存在"


def test_not_found_returns_404(no_raise_client):
    """不存在的路由应返回 FastAPI 默认 404"""
    resp = no_raise_client.get("/api/not-exist-path")
    assert resp.status_code == 404


def test_budget_exception_keeps_runtime_compatibility_and_existing_429_contract(no_raise_client):
    rejection = CallBudgetExceeded("External call budget exhausted")
    assert isinstance(rejection, BizException) and isinstance(rejection, RuntimeError)
    @no_raise_client.app.get("/test-budget")
    def stopped(): raise rejection
    response = no_raise_client.get("/test-budget")
    assert response.status_code == 429
    assert response.json() == {"success": False, "code": "OUTBOUND_BUDGET_EXHAUSTED", "message": str(rejection)}
