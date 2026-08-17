"""Wraps the `keyring` package for storing AI-provider API keys, admin
auth secrets, and the single saved database connection in OS-level secret
storage (Windows Credential Manager on this platform) - never in a
plaintext file. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 5 and
2026-08-15-database-ingestion-phase4c-design.md section 4.
"""
import json

import keyring

SERVICE_NAME = "kp-healthcare-plan"

PROVIDERS = ("anthropic", "openai", "gemini", "grok", "groq")

# Reserved keyring usernames for admin auth secrets and the single saved
# database connection - never listed as AI providers, and never accepted
# by get_key/set_key/delete_key.
ADMIN_PASSWORD_KEY = "admin_password_hash"
SESSION_SECRET_KEY = "session_secret"
DB_CONNECTION_KEY = "db_connection"
TELEGRAM_CONFIG_KEY = "telegram_config"


def _check_provider(provider):
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")


def get_key(provider):
    _check_provider(provider)
    return keyring.get_password(SERVICE_NAME, provider)


def set_key(provider, value):
    _check_provider(provider)
    keyring.set_password(SERVICE_NAME, provider, value)


def delete_key(provider):
    _check_provider(provider)
    try:
        keyring.delete_password(SERVICE_NAME, provider)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent - deleting a non-existent key is a no-op


def mask(value):
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def list_status():
    statuses = []
    for provider in PROVIDERS:
        value = get_key(provider)
        statuses.append({"provider": provider, "configured": bool(value), "hint": mask(value)})
    return statuses


def get_admin_password_hash():
    return keyring.get_password(SERVICE_NAME, ADMIN_PASSWORD_KEY)


def set_admin_password_hash(value):
    keyring.set_password(SERVICE_NAME, ADMIN_PASSWORD_KEY, value)


def get_session_secret():
    return keyring.get_password(SERVICE_NAME, SESSION_SECRET_KEY)


def set_session_secret(value):
    keyring.set_password(SERVICE_NAME, SESSION_SECRET_KEY, value)


def get_db_connection():
    raw = keyring.get_password(SERVICE_NAME, DB_CONNECTION_KEY)
    if raw is None:
        return None
    return json.loads(raw)


def set_db_connection(conn_info):
    keyring.set_password(SERVICE_NAME, DB_CONNECTION_KEY, json.dumps(conn_info))


def delete_db_connection():
    try:
        keyring.delete_password(SERVICE_NAME, DB_CONNECTION_KEY)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent - deleting a non-existent entry is a no-op


def get_telegram_config():
    raw = keyring.get_password(SERVICE_NAME, TELEGRAM_CONFIG_KEY)
    if raw is None:
        return None
    return json.loads(raw)


def set_telegram_config(config):
    keyring.set_password(SERVICE_NAME, TELEGRAM_CONFIG_KEY, json.dumps(config))


def delete_telegram_config():
    try:
        keyring.delete_password(SERVICE_NAME, TELEGRAM_CONFIG_KEY)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent - deleting a non-existent entry is a no-op
