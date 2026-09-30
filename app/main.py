"""FastAPI 应用入口。

启动时自动建表并（可选）幂等写入演示回收点。
"""

from __future__ import annotations

import logging
from datetime import date, time

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app import crud
from app.api import router
from app.config import settings
from app.database import init_db
from app.errors import (
    APIError,
    api_error_handler,
    validation_error_handler,
)
from app.schemas import (
    ClosureIn,
    ExtraOpeningIn,
    PointCreate,
    RuleSetIn,
    WeeklyPeriodIn,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("recycle")


async def _seed_demo() -> None:
    """幂等写入演示数据：一个普通点 + 一个美东点（覆盖 DST 场景）。"""

    demo_specs = [
        PointCreate(
            code="lake-garden",
            name="湖畔花园社区回收点",
            timezone="Asia/Shanghai",
        ),
        PointCreate(
            code="brooklyn-center",
            name="Brooklyn Community Recycling Point",
            timezone="America/New_York",
        ),
    ]

    created: dict[str, int] = {}
    session = await crud.read_session()
    try:
        from sqlalchemy import select

        from app.models import Point

        for spec in demo_specs:
            existing = await session.scalar(
                select(Point).where(Point.code == spec.code)
            )
            if existing is None:
                point = await crud.create_point(session, spec)
                await session.commit()
                created[spec.code] = point.id
                logger.info("seeded demo point %s (id=%s)", spec.code, point.id)
            else:
                created[spec.code] = existing.id
    finally:
        await session.close()

    if not created:
        return

    # 湖畔花园：周一/三/五 09:00-12:00，周六 20:00-次日02:00（跨午夜）；
    # 2026-10-01 国庆停开，当天下午补开 14:00-17:00。
    if "lake-garden" in created:
        try:
            await crud.publish_rules(
                created["lake-garden"],
                RuleSetIn(
                    base_version=0,
                    note="初始规则（演示）",
                    weekly_periods=[
                        WeeklyPeriodIn(weekday=1, start=time(9), end=time(12)),
                        WeeklyPeriodIn(weekday=3, start=time(9), end=time(12)),
                        WeeklyPeriodIn(weekday=5, start=time(9), end=time(12)),
                        WeeklyPeriodIn(
                            weekday=6,
                            start=time(20),
                            end=time(2),
                            end_day_offset=1,
                        ),
                    ],
                    closures=[
                        ClosureIn(day=date(2026, 10, 1), reason="国庆节"),
                    ],
                    extra_openings=[
                        ExtraOpeningIn(
                            day=date(2026, 10, 1),
                            start=time(14),
                            end=time(17),
                        ),
                    ],
                ),
            )
        except Exception as exc:  # 并发/已发布时忽略
            logger.info("demo seed skipped for lake-garden: %s", exc)

    # Brooklyn：DST 演示。周四 01:30-04:00（春季缺口 / 秋季重复都会命中），
    # 周六 23:30-次日 06:00（跨午夜覆盖整个回退夜）。
    if "brooklyn-center" in created:
        try:
            await crud.publish_rules(
                created["brooklyn-center"],
                RuleSetIn(
                    base_version=0,
                    note="initial rules (DST demo)",
                    weekly_periods=[
                        WeeklyPeriodIn(
                            weekday=4,
                            start=time(1, 30),
                            end=time(4),
                        ),
                        WeeklyPeriodIn(
                            weekday=6,
                            start=time(23, 30),
                            end=time(6),
                            end_day_offset=1,
                        ),
                    ],
                    closures=[
                        ClosureIn(day=date(2026, 11, 26), reason="Thanksgiving"),
                    ],
                    extra_openings=[],
                ),
            )
        except Exception as exc:  # pragma: no cover
            logger.info("demo seed skipped for brooklyn-center: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    if settings.seed_demo:
        await _seed_demo()
    yield


app = FastAPI(
    title="社区回收点服务日历 API",
    version="1.0.0",
    description=(
        "维护回收点 IANA 时区、每周开放时段、全天停开与当日补开；"
        "规则按版本号原子发布，查询基于单个已发布版本并返回 UTC 区间。"
    ),
    lifespan=lifespan,
)

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.include_router(router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
