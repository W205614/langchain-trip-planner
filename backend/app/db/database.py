"""SQLAlchemy 数据库配置 (SQLite)"""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import get_settings

# backend/data 目录 (数据库 + 向量库统一放这里)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite 数据库文件
DB_PATH = DATA_DIR / "trip_planner.db"
database_url = get_settings().database_url or f"sqlite:///{DB_PATH}"

# check_same_thread=False: FastAPI 会把同步端点放到线程池执行,
# 不同线程可能复用同一个会话, 需要关闭 SQLite 的同线程检查。
engine_options = {"echo": False, "pool_pre_ping": True}
if database_url.startswith("sqlite"):
    # SQLite 本地开发/单机 Docker 允许线程池内跨线程使用连接。
    engine_options["connect_args"] = {"check_same_thread": False}
engine = create_engine(database_url, **engine_options)

# autocommit=False: 事务需显式 commit, 便于依赖注入统一管理
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """ORM 模型基类"""


def get_db():
    """FastAPI 依赖: 请求级数据库会话 (用完自动归还/关闭)"""
    ensure_tables()  # 仅开发 SQLite 保留零配置建表兜底
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_tables_ready = False


def ensure_tables() -> None:
    """开发模式 SQLite 的幂等建表兜底。

    PostgreSQL 或生产模式的 schema 只能由 Alembic 管理，避免运行中静默漂移。
    """
    global _tables_ready
    if _tables_ready:
        return
    if not database_url.startswith("sqlite") or get_settings().app_env.lower() == "production":
        return
    from . import models  # noqa: F401  确保模型注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
    _tables_ready = True


def init_db() -> None:
    """初始化开发 SQLite，或验证 Alembic 管理的外部数据库连通性。"""
    if database_url.startswith("sqlite") and get_settings().app_env.lower() != "production":
        ensure_tables()
        return
    check_database_ready()


def check_database_ready() -> None:
    """验证数据库连通性；生产环境同时拒绝未迁移到 head 的 schema。"""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        if get_settings().app_env.lower() != "production":
            return

        current_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    script = ScriptDirectory.from_config(Config(str(alembic_ini)))
    expected_revision = script.get_current_head()
    if current_revision != expected_revision:
        raise RuntimeError(
            "数据库迁移版本未就绪: "
            f"current={current_revision or 'missing'}, expected={expected_revision}"
        )
