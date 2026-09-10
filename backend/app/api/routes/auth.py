"""用户认证 API (注册 / 登录 / 当前用户)"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status, Request
from ...core.rate_limit import limiter
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from ...db.database import get_db
from ...db.models import User

router = APIRouter(prefix="/auth", tags=["用户认证"])

logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="用户名(3-32字符)")
    password: str = Field(..., min_length=6, max_length=64, description="密码(至少6位)")


class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool = False


@router.post("/register", summary="注册", response_model=TokenResponse)
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    """注册新用户, 成功即返回 JWT (自动登录)"""
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        )
    user = User(username=body.username, hashed_password=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在")
    db.refresh(user)
    logger.info(f"新用户注册: {user.username}")
    return TokenResponse(access_token=create_access_token(user.id, user.token_version), username=user.username, is_admin=user.is_admin)


@router.post("/login", summary="登录", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """用户名+密码登录, 返回 JWT"""
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    logger.info(f"用户登录: {user.username}")
    return TokenResponse(access_token=create_access_token(user.id, user.token_version), username=user.username, is_admin=user.is_admin)


@router.post("/logout", summary="撤销当前账号的全部登录会话")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from sqlalchemy import update
    db.execute(update(User).where(User.id == current_user.id).values(token_version=User.token_version + 1))
    db.commit()
    return {"success": True, "message": "该账号的所有旧登录凭证已失效"}


@router.get("/me", summary="当前登录用户")
def me(current_user: User = Depends(get_current_user)):
    """返回当前登录用户信息 (需 Bearer token)"""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at.strftime("%Y-%m-%d %H:%M:%S") if current_user.created_at else None,
    }
