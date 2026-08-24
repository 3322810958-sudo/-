from __future__ import annotations

import hashlib
import hmac
import re
import secrets


USERNAME_RE = re.compile(r"^[^\s/\\]{3,32}$")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def validate_username(username: str) -> str:
    value = str(username or "").strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("账号需为 3-32 个字符，且不能包含空格、斜杠")
    return value


def validate_new_password(password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("密码至少需要 8 个字符")
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise ValueError("密码必须同时包含字母和数字")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(size: int = 32) -> str:
    return secrets.token_urlsafe(size)
