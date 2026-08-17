"""FastAPI application factory. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md,
2026-08-16-telegram-connector-design.md section 5, and
2026-08-16-bundled-local-database-design.md section 3.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scripts.lib import local_db
from server import telegram_bot
from server.routes import admin, dashboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(local_db.ensure_running)
    await telegram_bot.start_bot_task()
    yield
    await telegram_bot.stop_bot_task()
    await asyncio.to_thread(local_db.stop)


def create_app():
    app = FastAPI(title="KP Healthcare Plan", lifespan=lifespan)
    app.include_router(dashboard.router)
    app.include_router(admin.router)
    return app


app = create_app()
