"""SQLAlchemy 数据库配置 (SQLite)"""

from pathlib import Path

from sqlalchemy import create_engine
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
    ensure_tables()  # 兜底: 首次使用时自动建表, 不依赖 lifespan
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_tables_ready = False


def ensure_tables() -> None:
    """幂等建表 (只执行一次)"""
    global _tables_ready
    if _tables_ready:
        return
    from . import models  # noqa: F401  确保模型注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
    _tables_ready = True


def init_db() -> None:
    """显式初始化数据库 (应用启动时调用, 语义清晰)"""
    ensure_tables()
