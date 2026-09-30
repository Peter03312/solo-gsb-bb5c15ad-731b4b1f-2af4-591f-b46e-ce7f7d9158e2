"""测试环境：使用内存 SQLite，在导入应用前固定 DATABASE_URL。"""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SEED_DEMO"] = "0"

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.database import get_engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Closure,
    ExtraOpening,
    Point,
    RuleVersion,
    WeeklyPeriod,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_db():
    await init_db(retries=1)
    yield
    await get_engine().dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(_prepare_db):
    # 子表带 ON DELETE CASCADE；显式逐表清空以兼容 SQLite 默认外键设置。
    engine = get_engine()
    async with engine.begin() as conn:
        for model in (ExtraOpening, Closure, WeeklyPeriod, RuleVersion, Point):
            await conn.execute(delete(model))
    yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
