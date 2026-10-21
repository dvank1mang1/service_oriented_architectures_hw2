import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select

from app.config import settings
from app.errors import ApiError
from app.models import RefreshSession, User

hasher = PasswordHasher()
dummy_hash = hasher.hash("not-a-real-account-password")


def now():
    return datetime.now(timezone.utc)


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def decode(token, kind):
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "jti", "type", "role"]},
        )
        if not isinstance(claims["sub"], str) or not isinstance(claims["jti"], str):
            raise ValueError("invalid token identifiers")
        uuid.UUID(claims["sub"])
        uuid.UUID(claims["jti"])
        if claims["type"] != kind or claims["role"] not in {"USER", "SELLER", "ADMIN"}:
            raise ValueError("wrong token type or role")
        return claims
    except jwt.ExpiredSignatureError:
        code = "TOKEN_EXPIRED" if kind == "access" else "REFRESH_TOKEN_INVALID"
        raise ApiError(401, code, "token expired") from None
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        code = "TOKEN_INVALID" if kind == "access" else "REFRESH_TOKEN_INVALID"
        raise ApiError(401, code, "invalid token") from None


def issue_tokens(session, user):
    current = now()
    tokens = {}
    for kind, lifetime in (
        ("access", timedelta(minutes=settings.access_minutes)),
        ("refresh", timedelta(days=settings.refresh_days)),
    ):
        token_id = uuid.uuid4()
        expiry = current + lifetime
        claims = {
            "sub": str(user.id),
            "role": user.role,
            "type": kind,
            "jti": str(token_id),
            "iat": current,
            "exp": expiry,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        }
        token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
        tokens[f"{kind}_token"] = token
        if kind == "refresh":
            session.add(
                RefreshSession(id=token_id, user_id=user.id, token_hash=token_hash(token), expires_at=expiry)
            )
    return {**tokens, "token_type": "bearer", "expires_in": settings.access_minutes * 60}


def authenticate(session, authorization):
    parts = (authorization or "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ApiError(401, "TOKEN_INVALID", "bearer access token required")
    claims = decode(parts[1], "access")
    user = session.get(User, uuid.UUID(claims["sub"]))
    if user is None or claims["role"] != user.role:
        raise ApiError(401, "TOKEN_INVALID", "account or role is no longer valid")
    return {"id": user.id, "role": user.role}


def rotate(session, token):
    claims = decode(token, "refresh")
    stored = session.scalar(
        select(RefreshSession).where(RefreshSession.id == uuid.UUID(claims["jti"])).with_for_update()
    )
    if (
        stored is None
        or stored.revoked
        or stored.expires_at <= now()
        or stored.token_hash != token_hash(token)
        or str(stored.user_id) != claims["sub"]
    ):
        raise ApiError(401, "REFRESH_TOKEN_INVALID", "refresh token is no longer valid")
    user = session.get(User, stored.user_id)
    stored.revoked = True
    return issue_tokens(session, user)


def require_role(user, *roles):
    if user["role"] not in roles:
        raise ApiError(403, "ACCESS_DENIED", "role does not permit this operation")
