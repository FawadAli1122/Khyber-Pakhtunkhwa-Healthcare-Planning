import keyring.errors
import pytest

from server import keystore


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        key = (service, username)
        if key not in self.data:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.data[key]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


def test_set_and_get_key(fake_store):
    keystore.set_key("anthropic", "sk-ant-abc123")
    assert keystore.get_key("anthropic") == "sk-ant-abc123"


def test_get_key_missing_returns_none(fake_store):
    assert keystore.get_key("openai") is None


def test_delete_key_removes_it(fake_store):
    keystore.set_key("groq", "gsk-xyz")
    keystore.delete_key("groq")
    assert keystore.get_key("groq") is None


def test_delete_key_missing_is_a_noop(fake_store):
    keystore.delete_key("gemini")  # must not raise


def test_unknown_provider_raises(fake_store):
    with pytest.raises(ValueError):
        keystore.get_key("bogus")
    with pytest.raises(ValueError):
        keystore.set_key("bogus", "x")
    with pytest.raises(ValueError):
        keystore.delete_key("bogus")


def test_mask_short_value():
    assert keystore.mask("abcd") == "****"


def test_mask_long_value():
    value = "sk-ant-api03-abcdefgh1234"
    assert keystore.mask(value) == "*" * (len(value) - 4) + "1234"


def test_mask_none():
    assert keystore.mask(None) is None


def test_list_status_reports_configured_and_hint(fake_store):
    keystore.set_key("anthropic", "sk-ant-abcd1234")
    statuses = keystore.list_status()
    by_provider = {s["provider"]: s for s in statuses}
    assert by_provider["anthropic"]["configured"] is True
    assert by_provider["anthropic"]["hint"] == "*" * (len("sk-ant-abcd1234") - 4) + "1234"
    assert by_provider["openai"]["configured"] is False
    assert by_provider["openai"]["hint"] is None
    assert {s["provider"] for s in statuses} == set(keystore.PROVIDERS)


def test_admin_password_hash_roundtrip(fake_store):
    assert keystore.get_admin_password_hash() is None
    keystore.set_admin_password_hash("salt$digest")
    assert keystore.get_admin_password_hash() == "salt$digest"


def test_session_secret_roundtrip(fake_store):
    assert keystore.get_session_secret() is None
    keystore.set_session_secret("deadbeef")
    assert keystore.get_session_secret() == "deadbeef"


DB_CONN_INFO = {
    "host": "localhost", "port": 5432, "database": "kp_health",
    "user": "admin", "password": "s3cret", "sslmode": "prefer",
}


def test_db_connection_roundtrip(fake_store):
    assert keystore.get_db_connection() is None
    keystore.set_db_connection(DB_CONN_INFO)
    assert keystore.get_db_connection() == DB_CONN_INFO


def test_set_db_connection_overwrites_previous(fake_store):
    keystore.set_db_connection(DB_CONN_INFO)
    other = dict(DB_CONN_INFO, host="otherhost", database="other_db")
    keystore.set_db_connection(other)
    assert keystore.get_db_connection() == other


def test_delete_db_connection_removes_it(fake_store):
    keystore.set_db_connection(DB_CONN_INFO)
    keystore.delete_db_connection()
    assert keystore.get_db_connection() is None


def test_delete_db_connection_missing_is_a_noop(fake_store):
    keystore.delete_db_connection()  # must not raise


TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}


def test_telegram_config_roundtrip(fake_store):
    assert keystore.get_telegram_config() is None
    keystore.set_telegram_config(TELEGRAM_CONFIG)
    assert keystore.get_telegram_config() == TELEGRAM_CONFIG


def test_set_telegram_config_overwrites_previous(fake_store):
    keystore.set_telegram_config(TELEGRAM_CONFIG)
    other = dict(TELEGRAM_CONFIG, token="999999:XYZ")
    keystore.set_telegram_config(other)
    assert keystore.get_telegram_config() == other


def test_delete_telegram_config_removes_it(fake_store):
    keystore.set_telegram_config(TELEGRAM_CONFIG)
    keystore.delete_telegram_config()
    assert keystore.get_telegram_config() is None


def test_delete_telegram_config_missing_is_a_noop(fake_store):
    keystore.delete_telegram_config()  # must not raise
