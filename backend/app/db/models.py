"""ORM 数据模型"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
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
