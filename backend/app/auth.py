import os
import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")  # set a real secret in production
ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 8 * 60 * 60

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int, role: str, agent_id: int | None) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "agent_id": agent_id,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Returns {"id", "role", "agent_id"} or raises jwt.PyJWTError."""
    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    return {"id": int(payload["sub"]), "role": payload["role"], "agent_id": payload.get("agent_id")}


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise HTTPException(401, "Missing token. Log in first.")
    try:
        return decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token. Log in again.")


def require_roles(*roles: str):
    """Dependency factory: only users with one of `roles` get through."""
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"Requires role: {' or '.join(roles)}")
        return user
    return checker


def ensure_self_or_admin(user: dict, agent_id: int) -> None:
    if user["role"] == "admin":
        return
    if user["role"] == "agent" and user["agent_id"] == agent_id:
        return
    raise HTTPException(403, "You can only act on your own agent profile")
