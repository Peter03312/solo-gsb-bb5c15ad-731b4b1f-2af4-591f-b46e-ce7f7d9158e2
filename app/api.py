"""HTTP API 路由。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.config import settings
from app.errors import InvalidRequest
from app.schemas import (
    CalendarOut,
    PointCreate,
    PointOut,
    PublishResult,
    RuleSetIn,
    RuleVersionOut,
)
from app.scheduler import build_calendar, get_zone

router = APIRouter()


async def session_dep() -> AsyncSession:
    session = await crud.read_session()
    try:
        yield session
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# 回收点
# ---------------------------------------------------------------------------


@router.post("/points", response_model=PointOut, status_code=201, tags=["points"])
async def create_point(
    payload: PointCreate, session: AsyncSession = Depends(session_dep)
) -> PointOut:
    point = await crud.create_point(session, payload)
    await session.commit()
    await session.refresh(point)
    return PointOut.model_validate(point)


@router.get("/points", response_model=list[PointOut], tags=["points"])
async def list_points(session: AsyncSession = Depends(session_dep)) -> list[PointOut]:
    points = await crud.list_points(session)
    return [PointOut.model_validate(p) for p in points]


@router.get("/points/{point_id}", response_model=PointOut, tags=["points"])
async def get_point(
    point_id: int, session: AsyncSession = Depends(session_dep)
) -> PointOut:
    point = await crud.get_point(session, point_id)
    return PointOut.model_validate(point)


# ---------------------------------------------------------------------------
# 版本化规则
# ---------------------------------------------------------------------------


@router.put(
    "/points/{point_id}/rules",
    response_model=PublishResult,
    status_code=201,
    tags=["rules"],
)
async def put_rules(point_id: int, payload: RuleSetIn) -> PublishResult:
    """整版本快照 + 乐观锁原子发布。

    body.base_version 必须等于当前已发布版本号（首次发布传 0）；
    冲突时返回 409 version_conflict。
    """

    _point, version = await crud.publish_rules(point_id, payload)
    return PublishResult(
        point_id=point_id,
        version=version.version,
        note=version.note,
        created_at=version.created_at,
    )


@router.get(
    "/points/{point_id}/rules",
    response_model=RuleVersionOut,
    tags=["rules"],
)
async def get_published_rules(
    point_id: int, session: AsyncSession = Depends(session_dep)
) -> RuleVersionOut:
    """读取当前已发布版本（查询始终基于该快照）。"""

    _point, version = await crud.load_published(session, point_id)
    return version


@router.get(
    "/points/{point_id}/versions",
    response_model=list[RuleVersionOut],
    tags=["rules"],
)
async def list_versions(
    point_id: int, session: AsyncSession = Depends(session_dep)
) -> list[RuleVersionOut]:
    return await crud.list_versions(session, point_id)


@router.get(
    "/points/{point_id}/versions/{number}",
    response_model=RuleVersionOut,
    tags=["rules"],
)
async def get_version(
    point_id: int, number: int, session: AsyncSession = Depends(session_dep)
) -> RuleVersionOut:
    _point, version = await crud.load_version(session, point_id, number)
    return version


# ---------------------------------------------------------------------------
# 服务日历
# ---------------------------------------------------------------------------


@router.get(
    "/points/{point_id}/calendar",
    response_model=CalendarOut,
    tags=["calendar"],
)
async def get_calendar(
    point_id: int,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    session: AsyncSession = Depends(session_dep),
) -> CalendarOut:
    """按两端包含的当地日期范围查询实际开放区间（UTC 起止 + 当地日期 + 来源）。"""

    if date_to < date_from:
        raise InvalidRequest(
            "'to' must be the same as or later than 'from'",
            details={"from": date_from.isoformat(), "to": date_to.isoformat()},
        )
    span_days = (date_to - date_from).days + 1
    if span_days > settings.max_range_days:
        raise InvalidRequest(
            f"date range too large: {span_days} > {settings.max_range_days}"
        )

    point, version = await crud.load_published(session, point_id)
    zone = get_zone(point.timezone)
    ruleset = crud.to_ruleset(version)
    days = build_calendar(zone, ruleset, date_from, date_to)
    return CalendarOut(
        point_id=point.id,
        timezone=point.timezone,
        version=version.version,
        date_from=date_from,
        date_to=date_to,
        days=days,
    )
