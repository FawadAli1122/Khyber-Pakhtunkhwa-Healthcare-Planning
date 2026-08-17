import pytest

from server import auth, keystore


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        del self.data[(service, username)]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


def test_hash_and_verify_password_roundtrip():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("wrong password", hashed) is False


def test_hash_password_is_salted_differently_each_time():
    a = auth.hash_password("same password")
    b = auth.hash_password("same password")
    assert a != b


def test_is_admin_password_set_false_initially(fake_store):
    assert auth.is_admin_password_set() is False


def test_set_admin_password_then_is_set(fake_store):
    auth.set_admin_password("hunter2hunter2")
    assert auth.is_admin_password_set() is True


def test_verify_admin_password_correct(fake_store):
    auth.set_admin_password("hunter2hunter2")
    assert auth.verify_admin_password("hunter2hunter2") is True


def test_verify_admin_password_wrong(fake_store):
    auth.set_admin_password("hunter2hunter2")
    assert auth.verify_admin_password("nope") is False


def test_verify_admin_password_when_unset(fake_store):
    assert auth.verify_admin_password("anything") is False


def test_get_session_secret_generated_once_and_stable(fake_store):
    first = auth.get_session_secret()
    second = auth.get_session_secret()
    assert first == second
    assert len(first) == 32
    assert isinstance(first, bytes)


def test_session_cookie_roundtrip_valid():
    secret = b"x" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    assert auth.verify_session_cookie(cookie, secret, now=1000.0 + 60) is True


def test_session_cookie_expired():
    secret = b"x" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    assert auth.verify_session_cookie(cookie, secret, now=1000.0 + auth.SESSION_TTL_SECONDS + 1) is False


def test_session_cookie_wrong_secret_rejected():
    secret = b"x" * 32
    other_secret = b"y" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    assert auth.verify_session_cookie(cookie, other_secret, now=1000.0) is False


def test_session_cookie_tampered_payload_rejected():
    secret = b"x" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    _, _, signature = cookie.partition(".")
    tampered = f"9999999999.{signature}"
    assert auth.verify_session_cookie(tampered, secret, now=1000.0) is False


def test_verify_session_cookie_malformed_input():
    secret = b"x" * 32
    assert auth.verify_session_cookie("", secret) is False
    assert auth.verify_session_cookie("no-dot-here", secret) is False
    assert auth.verify_session_cookie(None, secret) is False
