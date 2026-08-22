import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
if len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
JWT_ALGORITHM = "HS256"
JWT_ISSUER = os.getenv("JWT_ISSUER", "cps-backend").strip() or "cps-backend"
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "cps-clients").strip() or "cps-clients"
ACCESS_TOKEN_EXPIRE_MINUTES = max(5, min(int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")), 120))
REFRESH_TOKEN_EXPIRE_DAYS = max(1, min(int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "14")), 30))

# V120 BUILD05: token revoke storage abstraction.
# Uses memory fallback by default; production can replace this adapter with Redis.
class TokenRevokeStore:
    def __init__(self):
        # BUILD06: keep revoke metadata with expiry so future Redis adapter can use TTL directly.
        self.access_tokens: dict[str, datetime] = {}
        self.refresh_tokens: dict[str, datetime] = {}

    def _cleanup(self, store: dict[str, datetime]) -> None:
        now = datetime.now(timezone.utc)
        expired = [k for k, v in store.items() if v <= now]
        for key in expired:
            store.pop(key, None)

    def revoke_access(self, jti: str, expires_at: datetime | None = None) -> None:
        if jti:
            self.access_tokens[jti] = expires_at or (datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    def revoke_refresh(self, jti: str, expires_at: datetime | None = None) -> None:
        if jti:
            self.refresh_tokens[jti] = expires_at or (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))

    def is_access_revoked(self, jti: str | None) -> bool:
        self._cleanup(self.access_tokens)
        return bool(jti and jti in self.access_tokens)

    def is_refresh_revoked(self, jti: str | None) -> bool:
        self._cleanup(self.refresh_tokens)
        return bool(jti and jti in self.refresh_tokens)

    def revoke_all(self, jtis: list[str]) -> None:
        """BUILD09: batch revoke support for logout-all / security events."""
        now = datetime.now(timezone.utc)
        for jti in jtis:
            if jti:
                self.access_tokens[jti] = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
                self.refresh_tokens[jti] = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)



TOKEN_STORE = TokenRevokeStore()

def revoke_token(jti: str) -> None:
    TOKEN_STORE.revoke_access(jti)

def is_token_revoked(jti: str | None) -> bool:
    return TOKEN_STORE.is_access_revoked(jti)

def revoke_refresh_token(jti: str) -> None:
    TOKEN_STORE.revoke_refresh(jti)

def revoke_from_payload(payload: dict) -> None:
    """BUILD09: revoke active token payload during logout/security response."""
    jti = payload.get("jti") if payload else None
    token_type = payload.get("token_type") if payload else None
    if not jti:
        return
    if token_type == "refresh":
        TOKEN_STORE.revoke_refresh(jti)
    else:
        TOKEN_STORE.revoke_access(jti)

def is_refresh_revoked(jti: str | None) -> bool:
    return TOKEN_STORE.is_refresh_revoked(jti)


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


def create_token(username: str, role: str, actor_type: str = "admin", actor_id: int | None = None, token_type: str = "access") -> str:
    now = datetime.now(timezone.utc)
    expires = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS) if token_type == "refresh" else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "role": role,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "token_type": token_type,
        "jti": str(uuid4()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + expires,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(username: str, role: str, actor_type: str = "admin", actor_id: int | None = None) -> str:
    return create_token(username, role, actor_type, actor_id, token_type="refresh")


def decode_refresh_token(token: str) -> dict:
    """BUILD07: refresh token validation entry point with revoke check."""
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            leeway=5,
            options={"require": ["exp", "iat", "sub", "jti", "iss", "aud", "actor_type", "token_type"]},
        )
        if payload.get("token_type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 类型错误")
        if is_refresh_revoked(payload.get("jti")):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh Token 已撤销")
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh Token 无效")


def rotate_refresh_token(payload: dict) -> tuple[str, str]:
    """BUILD07: rotate refresh token to reduce replay window."""
    old_jti = payload.get("jti")
    if old_jti:
        exp = payload.get("exp")
        revoke_refresh_token(old_jti)
    new_access = create_token(
        payload.get("sub"),
        payload.get("role", ""),
        payload.get("actor_type", "admin"),
        payload.get("actor_id"),
        token_type="access",
    )
    new_refresh = create_refresh_token(
        payload.get("sub"),
        payload.get("role", ""),
        payload.get("actor_type", "admin"),
        payload.get("actor_id"),
    )
    return new_access, new_refresh


def _payload(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            leeway=5,
            options={"require": ["exp", "iat", "sub", "jti", "iss", "aud", "actor_type", "token_type"]}
        )
        if is_token_revoked(payload.get("jti")):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已撤销")
        if payload.get("token_type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 类型错误")
        return payload
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
