"""核心排程引擎（纯函数、无数据库/框架依赖）。

规则模型
========

* ``WeeklyRule`` —— 每周固定时段。``end_day_offset=1`` 表示结束在次日，
  即“跨午夜”时段；跨午夜时段按 **开始日** 匹配星期规则。
* ``ClosureDate`` —— 指定日期全天停开。
* ``ExtraRule`` —— 指定日期的当日补开时段（不跨日）。

逐日裁切规则
============

对每个当地日期 ``d``，构成开放区间的片段来自：

1. 开始于 ``d`` 的常规时段（当晚部分）；
2. 开始于 ``d-1`` 的跨午夜常规时段（前夜延续到 ``d`` 凌晨的部分）；
3. ``d`` 当天的补开时段。

当 ``d`` 为停开日时，扣除属于 ``d`` 的全部常规片段——包括开始于 ``d`` 的当晚
片段（以及它向 ``d+1`` 的延续）和开始于 ``d-1`` 的前夜延续。实现上：
若某常规时段的开始日是停开日，则该时段产生的全部片段都被扣除；若 ``d`` 本身
是停开日，则其前夜延续也被扣除。补开时段照常叠加，不受停开影响。

夏令时（zoneinfo / IANA 时区）
==============================

* 缺失的墙上时间（春季跳时落入缺口）：边界顺延到缺口结束后的 **首个有效时刻**。
* 重复的墙上时间（秋季回退）：起点取 **较早实例**（fold=0），终点取
  **较晚实例**（fold=1）。

区间合并后保留全部来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class UnknownTimeZone(Exception):
    """IANA 时区名无法解析。"""


def get_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - 兜底
        raise UnknownTimeZone(tz_name) from exc


class InvalidPeriod(Exception):
    """时段本身非法（周几、跨日偏移或时长不合法）。"""


# ---------------------------------------------------------------------------
# 规则数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeeklyRule:
    """每周固定开放时段。

    weekday: ISO 星期几，1=周一 … 7=周日。
    start / end: 当地墙上时间。
    end_day_offset: 0 表示当日结束，1 表示次日结束（跨午夜，end 可为 00:00:xx）。
    """

    weekday: int
    start: time
    end: time
    end_day_offset: int = 0
    id: int | None = None

    def validate(self) -> None:
        if not 1 <= self.weekday <= 7:
            raise InvalidPeriod(f"weekday must be 1..7, got {self.weekday!r}")
        if self.end_day_offset not in (0, 1):
            raise InvalidPeriod("end_day_offset must be 0 or 1")
        if self.end.tzinfo is not None or self.start.tzinfo is not None:
            raise InvalidPeriod("local times must be timezone-naive")
        duration = (
            datetime.combine(date(2000, 1, 1 + self.end_day_offset), self.end)
            - datetime.combine(date(2000, 1, 1), self.start)
        )
        if self.end_day_offset == 0 and duration <= timedelta(0):
            raise InvalidPeriod("non-spanning period must end after start")
        if duration <= timedelta(0):
            raise InvalidPeriod("period duration must be positive")
        if duration > timedelta(hours=24):
            raise InvalidPeriod("regular periods must not exceed 24 hours")


@dataclass(frozen=True)
class ExtraRule:
    """指定日期的当日补开时段（不跨日，start < end）。"""

    day: date
    start: time
    end: time
    id: int | None = None

    def validate(self) -> None:
        if self.end <= self.start:
            raise InvalidPeriod("extra opening must end after start")
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise InvalidPeriod("local times must be timezone-naive")


@dataclass(frozen=True)
class ClosureDate:
    day: date
    id: int | None = None
    reason: str | None = None


@dataclass
class RuleSet:
    """某一版本下某回收点的完整规则集合。"""

    weekly: list[WeeklyRule] = field(default_factory=list)
    extras: list[ExtraRule] = field(default_factory=list)
    closures: set[date] = field(default_factory=set)


# ---------------------------------------------------------------------------
# 来源标记
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """开放区间的来源。kind: weekly | extra"""

    kind: str
    rule_id: int | None
    weekday: int | None = None
    day: date | None = None
    fragment: str | None = None  # evening(当晚) / morning(前夜延续) / full

    def as_dict(self) -> dict:
        d: dict = {
            "kind": self.kind,
            "rule_id": self.rule_id,
        }
        if self.weekday is not None:
            d["weekday"] = self.weekday
        if self.day is not None:
            d["day"] = self.day.isoformat()
        if self.fragment is not None:
            d["fragment"] = self.fragment
        return d


# ---------------------------------------------------------------------------
# 墙上时间 -> UTC 时刻（含 DST 处理）
# ---------------------------------------------------------------------------


def _gap_transition(zone: ZoneInfo, wall: datetime) -> datetime:
    """wall 位于春季跳时缺口内时，返回缺口结束的首个有效 UTC 时刻。

    zoneinfo 对缺口时间：fold=1 给出按“跳时前偏移”解释的时刻（过渡前），
    fold=0 给出按“跳时后偏移”解释的时刻（过渡后）。在 UTC 空间二分，
    找到偏移首次翻转为跳时后偏移的瞬间 T——即首个有效时刻。
    """

    lo = wall.replace(fold=1).astimezone(timezone.utc)  # 过渡前偏移
    hi = wall.replace(fold=0).astimezone(timezone.utc)  # 过渡后偏移
    post_offset = hi.astimezone(zone).utcoffset()

    def offset_at(t: datetime) -> timedelta:
        return t.astimezone(zone).utcoffset()

    # 罕见时区：上界仍处于旧偏移时，向上扩大 bracket 直到偏移翻转。
    for _ in range(10):
        if offset_at(hi) != post_offset:
            hi += timedelta(hours=24)
        else:
            break

    for _ in range(40):
        mid = lo + (hi - lo) / 2
        if offset_at(mid) == post_offset:
            hi = mid
        else:
            lo = mid
    return hi


def resolve_local(zone: ZoneInfo, day: date, t: time, prefer: str) -> datetime:
    """把当地墙上时间解析为带时区的 UTC datetime。

    prefer = "earlier" / "later"：仅对秋季回退的重复时间有效，
    分别取 fold=0（较早）/ fold=1（较晚）实例。
    春季跳时缺口内的时间，顺延到首个有效时刻。
    """

    wall = datetime.combine(day, t, tzinfo=zone)
    fold = 0 if prefer == "earlier" else 1
    candidate = wall.replace(fold=fold)
    back = candidate.astimezone(timezone.utc).astimezone(zone)
    if (
        back.replace(tzinfo=None) == wall.replace(tzinfo=None)
        and back.fold == candidate.fold
    ):
        return candidate.astimezone(timezone.utc)
    # round-trip 失败 => 落入缺口，顺延到首个有效时刻。
    return _gap_transition(zone, wall)


# ---------------------------------------------------------------------------
# 逐日裁切
# ---------------------------------------------------------------------------

WALL_MIDNIGHT = time(0, 0)


def _resolved_interval(
    zone: ZoneInfo,
    start_day: date,
    start_t: time,
    end_day: date,
    end_t: time,
) -> tuple[datetime, datetime] | None:
    """解析一段 [start,end) 当地墙上区间；DST 处理后若为空则返回 None。"""

    start = resolve_local(zone, start_day, start_t, "earlier")
    end = resolve_local(zone, end_day, end_t, "later")
    if end <= start:
        return None
    return start, end


def _build_day(
    d: date,
    zone: ZoneInfo,
    ruleset: RuleSet,
) -> list[dict]:
    closed = d in ruleset.closures
    items: list[tuple[datetime, datetime, Source]] = []

    # 1) 当晚开始于 d 的常规时段。
    if not closed:
        for rule in ruleset.weekly:
            if rule.weekday != d.isoweekday():
                continue
            if rule.end_day_offset == 1:
                bounds = _resolved_interval(
                    zone, d, rule.start, d + timedelta(days=1), rule.end
                )
                if bounds is not None:
                    items.append(
                        (
                            bounds[0],
                            bounds[1],
                            Source("weekly", rule.id, rule.weekday, d, "evening"),
                        )
                    )
            else:
                bounds = _resolved_interval(zone, d, rule.start, d, rule.end)
                if bounds is not None:
                    items.append(
                        (
                            bounds[0],
                            bounds[1],
                            Source("weekly", rule.id, rule.weekday, d, "full"),
                        )
                    )

    # 2) 前夜（d-1）开始的跨午夜时段延续到 d 凌晨。
    prev = d - timedelta(days=1)
    if not closed and prev not in ruleset.closures:
        for rule in ruleset.weekly:
            if rule.end_day_offset != 1 or rule.weekday != prev.isoweekday():
                continue
            bounds = _resolved_interval(zone, prev, rule.start, d, rule.end)
            if bounds is not None:
                items.append(
                    (
                        bounds[0],
                        bounds[1],
                        Source("weekly", rule.id, rule.weekday, prev, "morning"),
                    )
                )

    # 3) 当日补开（停开日也照常叠加）。
    for extra in ruleset.extras:
        if extra.day != d:
            continue
        bounds = _resolved_interval(zone, d, extra.start, d, extra.end)
        if bounds is not None:
            items.append(
                (
                    bounds[0],
                    bounds[1],
                    Source("extra", extra.id, None, d, None),
                )
            )

    return _merge(items, d, zone)


def _merge(
    items: list[tuple[datetime, datetime, Source]],
    d: date,
    zone: ZoneInfo,
) -> list[dict]:
    """按起点排序，合并相互重叠/首尾相接的区间并保留全部来源。"""

    items.sort(key=lambda x: (x[0], x[1]))
    merged: list[dict] = []
    for start, end, source in items:
        if merged and start <= merged[-1]["_end"]:
            if end > merged[-1]["_end"]:
                merged[-1]["_end"] = end
            sources: list[Source] = merged[-1]["_sources"]
            if source not in sources:
                sources.append(source)
        else:
            merged.append({"_end": end, "_sources": [source], "_start": start})

    result: list[dict] = []
    for m in merged:
        start: datetime = m["_start"]
        end: datetime = m["_end"]
        local_start = start.astimezone(zone)
        local_end = end.astimezone(zone)
        result.append(
            {
                "start_utc": start,
                "end_utc": end,
                "local_start": local_start.strftime("%Y-%m-%dT%H:%M:%S"),
                "local_end": local_end.strftime("%Y-%m-%dT%H:%M:%S"),
                "sources": [s.as_dict() for s in m["_sources"]],
            }
        )
    return result


def build_calendar(
    zone: ZoneInfo,
    ruleset: RuleSet,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """按两端包含的当地日期范围逐日裁切，返回每日实际开放区间。"""

    days: list[dict] = []
    cur = date_from
    while cur <= date_to:
        intervals = _build_day(cur, zone, ruleset)
        days.append(
            {
                "local_date": cur,
                "closed": cur in ruleset.closures,
                "intervals": intervals,
            }
        )
        cur += timedelta(days=1)
    return days
