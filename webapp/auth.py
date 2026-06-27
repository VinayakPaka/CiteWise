"""Authentication for the CiteWise web app.

Email + password accounts, with passwords hashed using the standard library
(``hashlib.pbkdf2_hmac`` — no extra dependency). A successful login mints a JSON
Web Token (JWT, HS256) that travels in an httponly cookie.

The token lives in a cookie rather than ``localStorage`` on purpose: the research
stream uses Server-Sent Events (``EventSource``), which cannot attach an
``Authorization: Bearer`` header — so a cookie is the only transport that works
for every request, and httponly keeps the token out of reach of page scripts.

A guest path (type a name, no password) is intentionally kept so a forgotten
password or flaky demo Wi-Fi never results in a dead app on stage.

Flow:
    POST /auth/signup  -> create an account, set the JWT cookie, return the user
    POST /auth/login   -> verify the password, set the JWT cookie
    POST /auth/demo    -> guest login (no password) when CITEWISE_ALLOW_GUEST is on
    POST /auth/logout  -> clear the cookie
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Any, Optional

import jwt
from fastapi import Request
from fastapi.responses import Response

import config
from webapp import db

SESSION_COOKIE = "citewise_session"

# Password hashing (PBKDF2-HMAC-SHA256). 200k rounds is a sensible 2024+ floor
# for an interactive login and stays well under a second on a laptop.
_PBKDF2_ALGO = "sha256"
_PBKDF2_ROUNDS = 200_000

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> tuple[str, str]:
    """Return ``(hex_digest, hex_salt)`` for a new password."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode(), salt, _PBKDF2_ROUNDS)
    return digest.hex(), salt.hex()


def verify_password(password: str, hex_digest: Optional[str], hex_salt: Optional[str]) -> bool:
    """Constant-time check of ``password`` against a stored digest + salt."""
    if not hex_digest or not hex_salt:
        return False
    candidate = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode(), bytes.fromhex(hex_salt), _PBKDF2_ROUNDS
    )
    return hmac.compare_digest(candidate.hex(), hex_digest)


# --------------------------------------------------------------------------- #
# JWT + session cookie
# --------------------------------------------------------------------------- #
def _make_token(user: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user["id"]),
        "email": user.get("email"),
        "name": user.get("name"),
        "iat": now,
        "exp": now + config.SESSION_DAYS * 86400,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALG)


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",            # blocks the cross-site POST/DELETE CSRF vectors
        secure=config.COOKIE_SECURE,  # True over HTTPS in prod (CITEWISE_COOKIE_SECURE)
        path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(SESSION_COOKIE, path="/")


def login_user(resp: Response, user: dict[str, Any]) -> None:
    """Attach a fresh JWT session for ``user`` to the response."""
    _set_session_cookie(resp, _make_token(user))


def logout(resp: Response) -> None:
    """End the session. JWTs are stateless, so this just clears the cookie."""
    clear_session_cookie(resp)


def current_user(request: Request) -> Optional[dict[str, Any]]:
    """Resolve the logged-in user from the JWT cookie (or None).

    The token's signature and expiry are verified, then the user is reloaded
    from the database — so a deleted account or a profile change is reflected
    immediately rather than trusting whatever was baked into the token.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALG])
    except jwt.PyJWTError:
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    return db.get_user_by_id(user_id)


# --------------------------------------------------------------------------- #
# Email accounts
# --------------------------------------------------------------------------- #
def signup(email: str, password: str, name: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Create an email account. Returns ``(user, None)`` or ``(None, error_code)``."""
    email = (email or "").strip().lower()
    password = password or ""
    name = (name or "").strip()[:60] or (email.split("@")[0] if email else "Researcher")

    if not _EMAIL_RE.match(email):
        return None, "invalid_email"
    if len(password) < _MIN_PASSWORD:
        return None, "weak_password"
    if db.get_user_by_email(email):
        return None, "email_taken"

    digest, salt = hash_password(password)
    return db.create_email_user(email, name, digest, salt), None


def login(email: str, password: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Verify credentials. Returns ``(user, None)`` or ``(None, error_code)``."""
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user or not verify_password(password or "", user.get("password_hash"), user.get("password_salt")):
        return None, "bad_credentials"
    return user, None


# --------------------------------------------------------------------------- #
# Guest login (fallback / frictionless demo)
# --------------------------------------------------------------------------- #
def guest_user(name: str) -> dict[str, Any]:
    """Create-or-reuse a guest user keyed by a slug of the chosen name.

    Caller is responsible for attaching the session via ``login_user``.
    """
    name = (name or "").strip()[:40] or "Guest"
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "guest"
    return db.upsert_user("guest", slug, name=name)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """Strip a user row down to what's safe to hand the browser.

    Notably excludes ``password_hash`` / ``password_salt``.
    """
    return {
        "id": user["id"],
        "name": user.get("name") or "Researcher",
        "email": user.get("email"),
        "picture": user.get("picture"),
        "provider": user.get("provider"),
    }
