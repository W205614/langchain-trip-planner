"""ORM 数据模型"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    """系统用户 (接口鉴权)

    用于 /api/auth/register /login 登录后签发 JWT, 保护 /api/history 等私有接口。
    密码存 bcrypt 哈希(不可逆), 绝不存明文。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(128))  # bcrypt 哈希
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class UserTravelPreference(Base):
    """用户主动保存的非敏感旅行偏好，不保存自由文本。"""

    __tablename__ = "user_travel_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    preferences: Mapped[str] = mapped_column(Text, default="[]")
    transportation: Mapped[str] = mapped_column(String(32), default="公共交通")
    accommodation: Mapped[str] = mapped_column(String(32), default="经济型酒店")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class TripRecord(Base):
    """旅行计划历史记录

    每次成功生成行程后自动保存一份, 归属登录用户 (user_id), 供该用户历史查询 + RAG 知识库使用。
    """

    __tablename__ = "trip_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)  # 归属用户
    city: Mapped[str] = mapped_column(String(64), index=True)
    start_date: Mapped[str] = mapped_column(String(16))
    end_date: Mapped[str] = mapped_column(String(16))
    travel_days: Mapped[int] = mapped_column(Integer, default=1)
    transportation: Mapped[str] = mapped_column(String(32), default="")
    accommodation: Mapped[str] = mapped_column(String(32), default="")
    preferences: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组字符串
    free_text_input: Mapped[str] = mapped_column(Text, default="")
    plan_json: Mapped[str] = mapped_column(Text)  # 完整行程计划 JSON
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class RagSyncJob(Base):
    """历史主数据到 Chroma 的最终一致性任务。

    不设外键：删除历史记录后仍须保留 delete 任务，才能清除派生向量。
    """

    __tablename__ = "rag_sync_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    operation: Mapped[str] = mapped_column(String(16))  # upsert / delete
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True
    )
    last_error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )


class KnowledgeDocument(Base):
    """用户提交、管理员审核后可公开检索的图文旅游资料。"""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submitted_by: Mapped[int] = mapped_column(Integer, index=True)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    city: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(160))
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(64))
    # 资料来源等级，不代表模型提取的每一条事实都已被逐条证明。
    source_tier: Mapped[str] = mapped_column(String(16), default="community", server_default="community")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    review_note: Mapped[str] = mapped_column(String(512), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    source_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class KnowledgeIngestJob(Base):
    """审核通过的知识资料解析与向量化 outbox。"""

    __tablename__ = "knowledge_ingest_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True
    )
    last_error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
