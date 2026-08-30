"""安全模块: JWT 签发/校验 + 密码哈希 + 依赖注入

- 密码: bcrypt 哈希(不可逆), 登录时比对。直接使用 bcrypt 库,
        避免 passlib 与 bcrypt>=4.1 的兼容性问题。
- Token: python-jose JWT (HS256), 携带 sub=用户ID, 过期时间 exp
- 依赖: get_current_user 供受保护接口使用, 未登录抛 401
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.database import SessionLocal, get_db
from ..db.models import User

logger = logging.getLogger(__name__)

# OAuth2 密码流: 前端从 /api/auth/login 拿 token, 之后 Authorization: Bearer <token>
# auto_error=False: 未带 token 时不自动抛错, 由 get_current_user 统一处理(返回401而非500)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_ALGO = "HS256"


def hash_password(plain: str) -> str:
    """明文密码 → bcrypt 哈希 (自动加盐, 输出格式 $2b$...)"""
    # bcrypt 只处理前72字节; 截断以避免超长密码抛错
    pw = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码是否匹配 bcrypt 哈希"""
    try:
        pw = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except (ValueError, TypeError) as e:
        logger.warning(f"密码校验失败(哈希格式异常): {e}")
        return False


def create_access_token(user_id: int) -> str:
    """签发 JWT: sub=用户ID, exp=当前时间+有效期"""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGO)


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 依赖: 解析 Bearer token → 返回当前用户

    未带 token / token 无效 / 用户不存在 → 401
    """
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(token, get_settings().jwt_secret_key, algorithms=[_ALGO])
        user_id = payload.get("sub")
        if user_id is None:
            raise cred_exc
    except JWTError:
        raise cred_exc

    user = db.get(User, int(user_id))
    if user is None:
        raise cred_exc
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """审核和发布公共知识只能由显式管理员执行。"""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


def bootstrap_admin_user() -> None:
    """仅将环境变量指定的既有账号提权，绝不在注册接口中自动授予角色。"""
    username = get_settings().bootstrap_admin_username.strip()
    if not username:
        return
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user and not user.is_admin:
            user.is_admin = True
            db.commit()
            logger.info("已授予配置管理员账号审核权限: %s", username)
    finally:
        db.close()
