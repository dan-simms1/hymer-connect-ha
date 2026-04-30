"""Helpers for identifying HYMER / EHG JWT tokens in local text captures."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Iterable

JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


def _pad_base64(value: str) -> str:
    return value + "=" * (-len(value) % 4)


def decode_jwt_without_verification(token: str) -> dict[str, Any]:
    """Decode a JWT header/payload without validating its signature."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        header = json.loads(
            base64.urlsafe_b64decode(_pad_base64(parts[0])).decode("utf-8")
        )
        payload = json.loads(
            base64.urlsafe_b64decode(_pad_base64(parts[1])).decode("utf-8")
        )
    except Exception:
        return {}
    return {"header": header, "payload": payload}


def iter_jwts(text: str) -> Iterable[str]:
    """Yield JWT-looking values from arbitrary local text."""
    for match in JWT_RE.finditer(text or ""):
        yield match.group(0)


def is_remote_access_refresh_token(token: str) -> bool:
    """Return True when a JWT is the long-lived EHG remote-access refresh token."""
    decoded = decode_jwt_without_verification(token)
    payload = decoded.get("payload", {})
    return isinstance(payload, dict) and payload.get("ett") == "access-refresh"


def find_remote_access_refresh_token(text: str) -> str | None:
    """Find the first remote-access refresh token in arbitrary local text."""
    for token in iter_jwts(text):
        if is_remote_access_refresh_token(token):
            return token
    return None


def coerce_remote_access_refresh_token(value: str) -> str:
    """Return a token from either a raw JWT or a larger captured text blob."""
    text = value.strip()
    if is_remote_access_refresh_token(text):
        return text
    extracted = find_remote_access_refresh_token(text)
    return extracted or text
