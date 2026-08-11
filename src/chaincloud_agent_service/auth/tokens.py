"""Small HMAC-signed bearer token helper.

This is intentionally lightweight for the local login MVP. It is not a full
JWT implementation, but it provides expiry and signature verification without
adding new dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _json_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def create_token(
    *,
    user_id: str,
    username: str,
    secret: str,
    expire_minutes: int,
) -> str:
    if not secret:
        raise ValueError("token secret is required")

    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + max(expire_minutes, 1) * 60,
    }

    header = {"alg": "HS256", "typ": "CHAINCLOUD_AUTH_TOKEN"}
    signing_input = b".".join(
        (
            _b64encode(_json_bytes(header)).encode("ascii"),
            _b64encode(_json_bytes(payload)).encode("ascii"),
        )
    )
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return signing_input.decode("ascii") + "." + _b64encode(signature)


def verify_token(token: str, *, secret: str) -> dict[str, Any] | None:
    if not token or not secret:
        return None

    try:
        header_raw, payload_raw, signature_raw = token.split(".", 2)
        signing_input = f"{header_raw}.{payload_raw}".encode("ascii")
        expected = hmac.new(
            secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        actual = _b64decode(signature_raw)
        if not hmac.compare_digest(actual, expected):
            return None

        header = json.loads(_b64decode(header_raw))
        if header.get("alg") != "HS256":
            return None

        payload = json.loads(_b64decode(payload_raw))
        exp = int(payload.get("exp", 0))
        if exp < int(time.time()):
            return None
        return payload
    except Exception:
        return None
