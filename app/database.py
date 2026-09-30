"""异步引擎 / 会话 / 建表初始化。"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import Base

logger = logging.getLogger("recycle.db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _async_url(url: str) -> str:
    # 允许直接写 postgresql:// / postgres://。
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        url = _async_url(settings.database_url)
        kwargs: dict = {"echo": False, "future": True}
        if url.startswith("sqlite"):
            # 内存 SQLite（测试）需要 StaticPool 才能跨连接共享。
            from sqlalchemy.pool import StaticPool

            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        _engine = create_async_engine(url, **kwargs)
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"


async def init_db(retries: int = 30, delay: float = 1.0) -> None:
    """建表；PostgreSQL 未就绪时重试，便于 compose 无 healthcheck 排序。"""

    engine = get_engine()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("database schema ready")
            return
        except Exception as exc:  # 连接失败则等待重试
            last_exc = exc
            logger.warning("init_db attempt %s/%s failed: %s", attempt, retries, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"database not reachable: {last_exc}")
