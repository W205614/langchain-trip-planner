"""FastAPI主应用"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from ..config import get_settings, validate_config, print_config
from ..core.logging import setup_logging
from ..core.exceptions import BizException, biz_exception_handler, global_exception_handler
from ..core.rate_limit import limiter
from ..db.database import check_database_ready, init_db
from .routes import trip, poi, map as map_routes, history, rag, auth


# 初始化日志(幂等): 控制台 + 文件落盘。
# 必须在 uvicorn 重配日志之前执行; reload 模式下子进程重新 import 本模块时也会执行, 保证任意模式日志可用。
setup_logging()

# 获取配置
settings = get_settings()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 (替代已弃用的 @app.on_event)

    startup: 打印横幅、打印配置并验证必要配置项
    shutdown: 打印关闭信息 (可在此释放资源, 如数据库连接池)
    """
    print("\n" + "=" * 60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("=" * 60)

    # 打印配置信息
    print_config()

    # 验证配置
    try:
        validate_config()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查.env文件并确保所有必要的配置项都已设置")
        raise

    print("\n" + "=" * 60)
    print(f"📚 API文档: http://localhost:{settings.port}/docs")
    print(f"📖 ReDoc文档: http://localhost:{settings.port}/redoc")
    print("=" * 60 + "\n")

    # 初始化数据库 (SQLite 建表, 幂等)
    try:
        init_db()
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")

    # 初始化 RAG 知识库 (自动索引 data/knowledge, 未配置 key 时自动降级)
    try:
        from ..services.rag_service import get_rag_service

        rag_service = get_rag_service()
        if rag_service.enabled:
            rag_service.ensure_knowledge_index()
            print(
                f"🧠 RAG 知识库已就绪: {rag_service._embedding.model} "
                f"(走中转 {rag_service._embedding.base_url}) + ChromaDB (知识库: data/knowledge)"
            )
    except Exception as e:
        print(f"⚠️ RAG 初始化失败(不影响主流程): {e}")

    from ..services.rag_sync import RagSyncWorker

    rag_sync_worker = RagSyncWorker()
    rag_sync_worker.start()
    app.state.rag_sync_worker = rag_sync_worker

    yield  # 应用运行期间挂起

    rag_sync_worker.stop()

    print("\n" + "=" * 60)
    print("👋 应用正在关闭...")
    print("=" * 60 + "\n")


# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="基于LangChain + FastAPI的智能旅行规划助手API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# 全局异常处理器: 业务异常(BizException)返回对应状态码, 其余异常统一500并记录完整堆栈
app.add_exception_handler(BizException, biz_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)
# 限流超限: 返回 429, 统一错误结构
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.state.limiter = limiter

# 可观测性: Prometheus 指标端点 /metrics (供监控系统抓取, 对接 Grafana 看板)
# 指标覆盖: 请求速率/延迟/错误率/HTTP状态码分布等, 按路由与方法打标签
instrumentator = Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """请求追踪: 生成 request_id + 记录耗时

    - 为每个请求生成唯一 request_id (支持透传客户端传入的 X-Request-ID)
    - 注入 request.state 供日志/异常处理器引用
    - 响应头返回 X-Request-ID / X-Process-Time, 便于排查与前端定位
    """
    start_time = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id

    response = await call_next(request)

    process_time = time.perf_counter() - start_time
    logger.info(
        f"请求耗时 | request_id={request_id} | 路径: {request.url.path} | "
        f"状态: {response.status_code} | 耗时: {process_time:.2f}s"
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{process_time:.2f}s"
    return response


# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(rag.router, prefix="/api")
app.include_router(auth.router, prefix="/api")


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.get("/healthz")
async def healthz():
    """存活检查：仅表示进程可响应，不访问任何依赖。"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
    }


@app.get("/readyz")
async def readyz():
    """就绪检查：数据库与本地 RAG 存储可用后才返回 healthy。"""
    try:
        check_database_ready()
        from ..services.rag_service import get_rag_service

        rag = get_rag_service()
        if rag.enabled:
            rag._knowledge_store._collection.count()
            rag._history_store._collection.count()
        return {"status": "healthy", "service": settings.app_name}
    except Exception as exc:
        logger.warning("就绪检查失败: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "unready", "service": settings.app_name},
        )


@app.get("/health")
async def health():
    """兼容旧健康检查地址。"""
    return await healthz()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
