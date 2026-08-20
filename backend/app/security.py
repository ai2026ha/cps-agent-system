import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import AdminUser, Agent, Player

ph = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is required")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    actor_type: str
    agent_pk: int | None = None
    agent_id: str | None = None


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except Exception:
        return False


def create_token(username: str, role: str, actor_type: str = "admin", actor_id: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _payload(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    payload = _payload(credentials)
    username = payload.get("sub")
    actor_type = payload.get("actor_type", "admin")

    if actor_type == "agent":
        actor_id = payload.get("actor_id")
        agent = db.get(Agent, actor_id) if actor_id else None
        if not agent or agent.username != username or agent.status != "active":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="代理账号不存在或已停用")
        return Principal(
            username=agent.username,
            role="agent",
            actor_type="agent",
            agent_pk=agent.id,
            agent_id=agent.agent_id,
        )

    if actor_type == "player":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="玩家账号不能访问代理后台")

    admin = db.query(AdminUser).filter(AdminUser.username == username, AdminUser.enabled.is_(True)).first()
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员不存在")
    return Principal(username=admin.username, role=admin.role, actor_type="admin")


def current_admin(
    principal: Principal = Depends(current_user),
    db: Session = Depends(get_db),
) -> AdminUser:
    if principal.actor_type != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该功能仅管理员可操作")
    admin = db.query(AdminUser).filter(AdminUser.username == principal.username, AdminUser.enabled.is_(True)).first()
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员不存在")
    return admin



def current_player(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Player:
    """玩家中心独立鉴权，玩家 token 不能访问代理后台。"""
    payload = _payload(credentials)
    if payload.get("actor_type") != "player":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请使用玩家账号登录")
    username = payload.get("sub")
    actor_id = payload.get("actor_id")
    player = db.get(Player, actor_id) if actor_id else None
    if not player or player.username != username or player.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="玩家账号不存在或已停用")
    return player

def current_channel_user(principal: Principal = Depends(current_user)) -> Principal:
    """渠道管理允许平台管理员和代理账号访问。"""
    return principal
