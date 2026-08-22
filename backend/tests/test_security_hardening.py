import os

os.environ.setdefault("JWT_SECRET", "test-secret-that-is-definitely-longer-than-32-characters")

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import backend_client_ip, enforce_rate_limit, _RATE_LIMIT_BUCKETS
from app.security import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, create_token, decode_refresh_token


def request_for(peer: str, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 1234)})


def test_forwarded_ip_is_ignored_without_trusted_proxy(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    assert backend_client_ip(request_for("10.0.0.2", "198.51.100.10")) == "10.0.0.2"


def test_forwarded_ip_uses_rightmost_untrusted_boundary(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert backend_client_ip(request_for("10.0.0.2", "203.0.113.9, 198.51.100.10")) == "198.51.100.10"


def test_rate_limit_rejects_excess_requests():
    _RATE_LIMIT_BUCKETS.clear()
    enforce_rate_limit("test", "127.0.0.1", 2, 60)
    enforce_rate_limit("test", "127.0.0.1", 2, 60)
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limit("test", "127.0.0.1", 2, 60)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_access_token_has_bound_issuer_and_audience():
    token = create_token("alice", "player", actor_type="player", actor_id=1)
    payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
    assert payload["iss"] == JWT_ISSUER
    assert payload["aud"] == JWT_AUDIENCE


def test_access_token_cannot_be_used_as_refresh_token():
    token = create_token("alice", "player", actor_type="player", actor_id=1)
    with pytest.raises(HTTPException):
        decode_refresh_token(token)
