"""Admin password hashing (stdlib PBKDF2) and signed session cookies (stdlib
HMAC) - see docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
section 6. No new dependency for either: the only new dependency is the
`keyring`-backed storage in keystore.py.
"""
import hashlib
import hmac
import secrets
import time

from server import keystore

PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 hours


def hash_password(password):
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    salt_hex, _, digest_hex = stored.partition("$")
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def is_admin_password_set():
    return keystore.get_admin_password_hash() is not None


def set_admin_password(password):
    keystore.set_admin_password_hash(hash_password(password))


def verify_admin_password(password):
    stored = keystore.get_admin_password_hash()
    if stored is None:
        return False
    return verify_password(password, stored)


def get_session_secret():
    secret_hex = keystore.get_session_secret()
    if secret_hex is None:
        secret_hex = secrets.token_bytes(32).hex()
        keystore.set_session_secret(secret_hex)
    return bytes.fromhex(secret_hex)


def create_session_cookie(secret, now=None):
    expires_at = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = str(expires_at)
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session_cookie(cookie, secret, now=None):
    if not cookie or "." not in cookie:
        return False
    payload, _, signature = cookie.partition(".")
    expected_signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return False
    try:
        expires_at = int(payload)
    except ValueError:
        return False
    current_time = now if now is not None else time.time()
    return current_time < expires_at
