"""pytest 全局配置

重要: 在 import 应用之前设置测试环境变量。
环境变量优先于 .env 文件(pydantic-settings 默认行为),
用假 key 隔离真实的高德/LLM 凭据, 保证测试不发任何真实外部请求。
"""

import os
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path, 允许 import app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试环境变量 (必须在 import app 之前设置)
os.environ["AMAP_API_KEY"] = "test_amap_key"
os.environ["AMAP_TRANSPORT"] = "rest"  # Existing offline REST fixtures; MCP has dedicated protocol tests.
os.environ["LLM_API_KEY"] = "test_llm_key"
os.environ["LLM_BASE_URL"] = "http://localhost:9999/v1"  # 无效端点, 防止误发请求
os.environ["LLM_MODEL_ID"] = "test-model"
# 开发机可能在 backend/.env 指向 PostgreSQL。测试必须独立于该本机配置，
# 否则收集阶段会因未安装的 PostgreSQL 驱动而失败。
import tempfile
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="trip-tests-"))
os.environ["DATA_DIR"] = str(_TEST_ROOT)
os.environ["CHROMA_DIR"] = str(_TEST_ROOT / "chroma")
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / "knowledge_uploads")
os.environ["LOG_DIR"] = str(_TEST_ROOT / "logs")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///" + (_TEST_ROOT / "test.db").as_posix())
os.environ["EMBEDDING_API_KEY"] = "test"
os.environ["EMBEDDING_BASE_URL"] = "http://127.0.0.1:9999/v1"
os.environ["VISION_API_KEY"] = "test"
os.environ["VISION_BASE_URL"] = "http://127.0.0.1:9999/v1"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = ""
os.environ["JWT_SECRET_KEY"] = "test-secret-only-01234567890123456789"
os.environ["APP_ENV"] = "development"
os.environ["RAG_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture(autouse=True, scope="session")
def reset_isolated_postgres():
    """Repeated integration runs start clean, without ever accepting a daily DB name."""
    from app.db.database import engine
    from app.db.models import Base
    from sqlalchemy import text
    if engine.dialect.name == "postgresql":
        if engine.url.database != "trip_tests":
            raise RuntimeError("Integration tests require the isolated trip_tests database")
        tables = ', '.join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE " + tables + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(autouse=True, scope="session")
def _isolate_logs():
    """测试期间隔离日志: 移除文件 handler。

    异常测试会故意触发异常(如 /test-uncaught), 全局异常处理器会把完整堆栈写入
    logs/app.log, 导致测试产物污染生产日志。测试只输出到控制台, 不落盘。
    """
    import logging

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)

    yield


@pytest.fixture(scope="session")
def client() -> TestClient:
    """测试客户端 (整个测试会话复用同一个应用实例)"""
    return TestClient(app)

@pytest.fixture(autouse=True)
def reset_rate_limits():
    from app.core.rate_limit import limiter
    limiter.reset()
