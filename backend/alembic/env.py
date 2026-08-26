"""Alembic 迁移环境

- 目标 metadata: 来自 app.db.models (自动检测 ORM 模型变更)
- 数据库 URL: 复用 app.db.database 的 SQLite 路径 (backend/data/trip_planner.db),
  保证迁移与运行时用同一个库
- 支持 Alembic 配置覆盖 sqlalchemy.url (如生产用 PostgreSQL 时在 alembic.ini 改)
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 将 backend 加入 sys.path, 允许 import app 包
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import DB_PATH  # noqa: E402  统一数据库路径
from app.db import models as _models  # noqa: E402,F401  注册全部模型到 Base.metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 强制使用应用同款 SQLite 数据库, 覆盖 alembic.ini 的占位 URL。
# 生产切换数据库时, 在 alembic.ini 的 sqlalchemy.url 覆盖即可 (此处仍以 ini 优先? 否——)
# 说明: 这里以应用 DB_PATH 为准, 保证迁移与运行时用同一个库。
config.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH.as_posix()}")

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata: 供 autogenerate 对比当前 ORM 模型
target_metadata = _models.Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite 需 batch 模式支持 ALTER 操作
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite 需 batch 模式
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
