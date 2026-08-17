"""Verifies server/app.py's lifespan starts/stops the Telegram bot task.
Every existing TestClient(create_app()) call elsewhere in this project's
test suite is unaffected - Starlette's TestClient only runs the ASGI
lifespan protocol when used as `with TestClient(app) as client:`, which
no pre-existing test in this project does.
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from scripts.lib import local_db
from server import telegram_bot
from server.app import create_app


def test_lifespan_starts_and_stops_the_bot_task(monkeypatch):
    start_mock = AsyncMock(return_value=True)
    stop_mock = AsyncMock()
    monkeypatch.setattr(telegram_bot, "start_bot_task", start_mock)
    monkeypatch.setattr(telegram_bot, "stop_bot_task", stop_mock)
    monkeypatch.setattr(local_db, "ensure_running", lambda: None)
    monkeypatch.setattr(local_db, "stop", lambda: None)

    with TestClient(create_app()) as client:
        client.get("/")  # exercise a request inside the running lifespan

    start_mock.assert_awaited_once()
    stop_mock.assert_awaited_once()


def test_lifespan_starts_and_stops_the_local_database(monkeypatch):
    ensure_running_calls = []
    stop_calls = []
    monkeypatch.setattr(local_db, "ensure_running", lambda: ensure_running_calls.append(1))
    monkeypatch.setattr(local_db, "stop", lambda: stop_calls.append(1))
    monkeypatch.setattr(telegram_bot, "start_bot_task", AsyncMock(return_value=True))
    monkeypatch.setattr(telegram_bot, "stop_bot_task", AsyncMock())

    with TestClient(create_app()) as client:
        client.get("/")

    assert ensure_running_calls == [1]
    assert stop_calls == [1]
