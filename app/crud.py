"""服务层：回收点管理、版本化规则的原子发布、规则装载。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_engine
from app.errors import (
    InvalidPeriodError,
    NoPublishedRules,
    PointNotFound,
    UnknownTimeZoneError,
    VersionConflict,
)
from app.models import Closure, ExtraOpening, Point, RuleVersion, WeeklyPeriod
from app.schemas import PointCreate, RuleSetIn
from app.scheduler import (
    ExtraRule,
    RuleSet,
    WeeklyRule,
    get_zone,
)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


async def new_transaction() -> AsyncSession:
    """写事务。

    使用 PG 默认 READ COMMITTED：``SELECT ... FOR UPDATE`` 在并发发布时让后到
    事务在锁释放后读到最新版本号，从而干净地返回 409；若改成
    REPEATABLE READ，PG 会直接抛 40001 序列化错误。``(point_id, version)``
    唯一约束作为最后防线。
    """

    return AsyncSession(get_engine(), expire_on_commit=False)


async def read_session() -> AsyncSession:
    return AsyncSession(get_engine(), expire_on_commit=False)


# ---------------------------------------------------------------------------
# 回收点
# ---------------------------------------------------------------------------


async def create_point(session: AsyncSession, payload: PointCreate) -> Point:
    try:
        get_zone(payload.timezone)
    except Exception as exc:
        raise UnknownTimeZoneError(
            f"unknown IANA timezone: {payload.timezone!r}"
        ) from exc

    point = Point(
        code=payload.code, name=payload.name, timezone=payload.timezone
    )
    session.add(point)
    await session.flush()
    return point


async def get_point(session: AsyncSession, point_id: int) -> Point:
    point = await session.get(Point, point_id)
    if point is None:
        raise PointNotFound(f"point {point_id} not found")
    return point


async def list_points(session: AsyncSession) -> list[Point]:
    result = await session.execute(select(Point).order_by(Point.id))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 规则发布（整版本快照 + 乐观锁 + 行锁）
# ---------------------------------------------------------------------------


def _build_entities(payload: RuleSetIn) -> tuple[list, set[date], list]:
    """校验全部时段并构造 ORM 子对象；任何非法时段都在写库前失败。"""

    weekly_keys: set[tuple] = set()
    weekly_entities: list[WeeklyPeriod] = []
    for p in payload.weekly_periods:
        if p.start.tzinfo is not None or p.end.tzinfo is not None:
            raise InvalidPeriodError("weekly times must be timezone-naive")
        rule = WeeklyRule(
            weekday=p.weekday,
            start=p.start.replace(tzinfo=None),
            end=p.end.replace(tzinfo=None),
            end_day_offset=p.end_day_offset,
        )
        try:
            rule.validate()
        except Exception as exc:
            raise InvalidPeriodError(str(exc)) from exc
        key = (p.weekday, p.start, p.end, p.end_day_offset)
        if key in weekly_keys:
            raise InvalidPeriodError(f"duplicate weekly period: {key}")
        weekly_keys.add(key)
        weekly_entities.append(
            WeeklyPeriod(
                weekday=p.weekday,
                start_time=p.start.replace(tzinfo=None),
                end_time=p.end.replace(tzinfo=None),
                end_day_offset=p.end_day_offset,
            )
        )

    closure_days: set[date] = set()
    closure_entities: list[Closure] = []
    for c in payload.closures:
        if c.day in closure_days:
            raise InvalidPeriodError(f"duplicate closure day: {c.day}")
        closure_days.add(c.day)
        closure_entities.append(Closure(day=c.day, reason=c.reason))

    extra_keys: set[tuple] = set()
    extra_entities: list[ExtraOpening] = []
    for e in payload.extra_openings:
        if e.start.tzinfo is not None or e.end.tzinfo is not None:
            raise InvalidPeriodError("extra times must be timezone-naive")
        rule = ExtraRule(day=e.day, start=e.start, end=e.end)
        try:
            rule.validate()
        except Exception as exc:
            raise InvalidPeriodError(str(exc)) from exc
        key = (e.day, e.start, e.end)
        if key in extra_keys:
            raise InvalidPeriodError(f"duplicate extra opening: {key}")
        extra_keys.add(key)
        extra_entities.append(
            ExtraOpening(
                day=e.day, start_time=e.start, end_time=e.end
            )
        )

    return weekly_entities, closure_days, closure_entities, extra_entities


async def publish_rules(
    point_id: int, payload: RuleSetIn
) -> tuple[Point, RuleVersion]:
    """按 base_version 乐观条件原子发布一整套规则。

    成功条件：point 存在且其当前 published_version == payload.base_version。
    整个写入（新版本 + 全部子行 + 翻转指针）在单个事务中提交。
    """

    weekly_entities, _, closure_entities, extra_entities = _build_entities(payload)

    session = await new_transaction()
    try:
        # SELECT ... FOR UPDATE：并发发布在此串行化，后到者看到最新版本号。
        point = await session.get(Point, point_id, with_for_update=True)
        if point is None:
            raise PointNotFound(f"point {point_id} not found")

        # 时区若已损坏（理论上不会）也明确报错。
        try:
            get_zone(point.timezone)
        except Exception as exc:  # pragma: no cover - 数据兜底
            raise UnknownTimeZoneError(point.timezone) from exc

        if point.published_version != payload.base_version:
            raise VersionConflict(
                "base_version does not match the current published version",
                details={
                    "base_version": payload.base_version,
                    "current_version": point.published_version,
                },
            )

        version = RuleVersion(
            point_id=point.id,
            version=point.published_version + 1,
            note=payload.note,
            weekly_periods=weekly_entities,
            closures=closure_entities,
            extra_openings=extra_entities,
        )
        session.add(version)
        await session.flush()  # 拿到 version.id

        point.published_version_id = version.id
        point.published_version = version.version

        await session.commit()
        # 刷新关系时间戳，供响应使用。
        await session.refresh(version)
        return point, version
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# 已发布版本读取
# ---------------------------------------------------------------------------


async def load_published(session: AsyncSession, point_id: int) -> tuple[Point, RuleVersion]:
    point = await session.get(Point, point_id)
    if point is None:
        raise PointNotFound(f"point {point_id} not found")
    if not point.published_version_id:
        raise NoPublishedRules(
            f"point {point_id} has no published rules yet",
            details={"point_id": point_id, "published_version": 0},
        )
    version = await session.scalar(
        select(RuleVersion)
        .where(RuleVersion.id == point.published_version_id)
        .options(
            selectinload(RuleVersion.weekly_periods),
            selectinload(RuleVersion.closures),
            selectinload(RuleVersion.extra_openings),
        )
    )
    # 快照必须存在；否则属于数据不一致。
    if version is None:  # pragma: no cover - 兜底
        raise NoPublishedRules(f"point {point_id} published snapshot missing")
    return point, version


async def load_version(
    session: AsyncSession, point_id: int, number: int
) -> tuple[Point, RuleVersion]:
    point = await get_point(session, point_id)
    version = await session.scalar(
        select(RuleVersion)
        .where(RuleVersion.point_id == point_id, RuleVersion.version == number)
        .options(
            selectinload(RuleVersion.weekly_periods),
            selectinload(RuleVersion.closures),
            selectinload(RuleVersion.extra_openings),
        )
    )
    if version is None:
        raise PointNotFound(
            f"version {number} of point {point_id} not found"
        )
    return point, version


async def list_versions(session: AsyncSession, point_id: int) -> list[RuleVersion]:
    await get_point(session, point_id)
    result = await session.execute(
        select(RuleVersion)
        .where(RuleVersion.point_id == point_id)
        .options(
            selectinload(RuleVersion.weekly_periods),
            selectinload(RuleVersion.closures),
            selectinload(RuleVersion.extra_openings),
        )
        .order_by(RuleVersion.version)
    )
    return list(result.scalars().all())


def to_ruleset(version: RuleVersion) -> RuleSet:
    weekly = [
        WeeklyRule(
            id=p.id,
            weekday=p.weekday,
            start=p.start_time,
            end=p.end_time,
            end_day_offset=p.end_day_offset,
        )
        for p in sorted(
            version.weekly_periods,
            key=lambda p: (p.weekday, p.start_time, p.end_time, p.end_day_offset),
        )
    ]
    extras = [
        ExtraRule(id=e.id, day=e.day, start=e.start_time, end=e.end_time)
        for e in sorted(version.extra_openings, key=lambda e: (e.day, e.start_time))
    ]
    closures = {c.day for c in version.closures}
    return RuleSet(
        weekly=weekly,
        extras=extras,
        closures=closures,
    )
