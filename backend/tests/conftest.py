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
os.environ.pop("DATABASE_URL", None)
os.environ["BUSINESS_URL"] = "http://127.0.0.1:9999"
os.environ["INTERNAL_SERVICE_KEY"] = "internal-test-key-01234567890123456789"
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
