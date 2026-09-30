"""排程引擎单元测试：跨午夜、停开/补开、区间合并、DST 缺口与重复。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.scheduler import (
    ExtraRule,
    RuleSet,
    WeeklyRule,
    build_calendar,
    get_zone,
    resolve_local,
)


def interval_utc(days: list[dict], d: date) -> list[tuple[str, str]]:
    for day in days:
        if day["local_date"] == d:
            return [
                (i["start_utc"].isoformat(), i["end_utc"].isoformat())
                for i in day["intervals"]
            ]
    raise AssertionError(f"day {d} not found")


def sources(days: list[dict], d: date) -> list[list[str]]:
    for day in days:
        if day["local_date"] == d:
            return [
                [s["kind"] + (":" + str(s.get("fragment") or "")) for s in i["sources"]]
                for i in day["intervals"]
            ]
    raise AssertionError(d)


# ---------------------------------------------------------------------------
# 常规 + 跨午夜
# ---------------------------------------------------------------------------


def test_simple_weekly_and_inclusive_range():
    # 2026-10-05 是周一，2026-10-11 是周日。
    zone = get_zone("Asia/Shanghai")
    rs = RuleSet(
        weekly=[
            WeeklyRule(id=1, weekday=1, start=time(9), end=time(12)),
            WeeklyRule(id=2, weekday=1, start=time(13, 30), end=time(18)),
        ]
    )
    days = build_calendar(zone, rs, date(2026, 10, 4), date(2026, 10, 6))
    # 周日无开放
    assert days[0]["intervals"] == []
    # 周一两段
    mon = days[1]["intervals"]
    assert [(i["local_start"], i["local_end"]) for i in mon] == [
        ("2026-10-05T09:00:00", "2026-10-05T12:00:00"),
        ("2026-10-05T13:30:00", "2026-10-05T18:00:00"),
    ]
    # UTC 偏移 +08:00
    assert mon[0]["start_utc"].isoformat() == "2026-10-05T01:00:00+00:00"
    assert len(days) == 3


def test_overnight_spans_midnight_by_start_weekday():
    zone = get_zone("Asia/Shanghai")
    # 周六 20:00 -> 次日 02:00
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=6, start=time(20), end=time(2), end_day_offset=1)]
    )
    days = build_calendar(zone, rs, date(2026, 10, 3), date(2026, 10, 5))
    sat, sun = days[0], days[1]
    # 10-03 周六当晚片段
    assert [(i["local_start"], i["local_end"]) for i in sat["intervals"]] == [
        ("2026-10-03T20:00:00", "2026-10-04T02:00:00")
    ]
    assert sources(days, date(2026, 10, 3)) == [["weekly:evening"]]
    # 周日没有任何“开始于周日”的规则 => 无输出（前夜片段只属于开始日裁切？）
    # 注意：按逐日裁切，前夜延续在周日也会以 morning 片段出现——见下一个测试。
    assert sources(days, date(2026, 10, 4)) == [["weekly:morning"]]
    assert [(i["local_start"], i["local_end"]) for i in sun["intervals"]] == [
        ("2026-10-03T20:00:00", "2026-10-04T02:00:00")
    ]


def test_closure_removes_evening_and_morning_pieces():
    zone = get_zone("Asia/Shanghai")
    # 周五 09-12；周六 20->次日02。
    rs = RuleSet(
        weekly=[
            WeeklyRule(id=1, weekday=5, start=time(9), end=time(12)),
            WeeklyRule(id=2, weekday=6, start=time(20), end=time(2), end_day_offset=1),
        ],
        closures={
            date(2026, 10, 2),   # 周五停开：当晚常规消失
            date(2026, 10, 4),   # 周日停开：扣除前夜（周六开始）的延续
        },
    )
    days = build_calendar(zone, rs, date(2026, 10, 2), date(2026, 10, 4))
    fri, sat, sun = days
    assert fri["closed"] is True and fri["intervals"] == []
    assert sat["closed"] is False
    assert len(sat["intervals"]) == 1
    # 周日停开 => 连周六跨午夜时段在周日的延续也被整体扣除（周六当晚也不出现）
    assert sun["closed"] is True and sun["intervals"] == []
    assert sat["intervals"][0]["local_start"] == "2026-10-03T20:00:00"
    assert sat["intervals"][0]["local_end"] == "2026-10-04T02:00:00"


def test_closure_start_day_removes_whole_overnight_rule():
    zone = get_zone("Asia/Shanghai")
    # 周六 20->02，但周六本身停开 => 当晚与次日延续都不出现。
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=6, start=time(20), end=time(2), end_day_offset=1)],
        closures={date(2026, 10, 3)},
    )
    days = build_calendar(zone, rs, date(2026, 10, 3), date(2026, 10, 4))
    assert days[0]["intervals"] == []
    assert days[1]["intervals"] == []


def test_extra_opening_on_closure_day_is_kept():
    zone = get_zone("Asia/Shanghai")
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=5, start=time(9), end=time(12))],
        closures={date(2026, 10, 2)},
        extras=[ExtraRule(id=9, day=date(2026, 10, 2), start=time(14), end=time(17))],
    )
    days = build_calendar(zone, rs, date(2026, 10, 2), date(2026, 10, 2))
    day = days[0]
    assert day["closed"] is True
    assert [(i["local_start"], i["local_end"]) for i in day["intervals"]] == [
        ("2026-10-02T14:00:00", "2026-10-02T17:00:00")
    ]
    assert sources(days, date(2026, 10, 2)) == [["extra:"]]


def test_overlapping_intervals_merge_and_keep_all_sources():
    zone = get_zone("Asia/Shanghai")
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=1, start=time(9), end=time(11))],
        extras=[
            ExtraRule(id=10, day=date(2026, 10, 5), start=time(10, 30), end=time(12)),
            ExtraRule(id=11, day=date(2026, 10, 5), start=time(8), end=time(9)),
        ],
    )
    days = build_calendar(zone, rs, date(2026, 10, 5), date(2026, 10, 5))
    intervals = days[0]["intervals"]
    # 08:00-09:00 与 09:00-11:00 首尾相接 => 合并；补开 10:30-12:00 重叠 => 再合并。
    assert len(intervals) == 1
    iv = intervals[0]
    assert iv["local_start"] == "2026-10-05T08:00:00"
    assert iv["local_end"] == "2026-10-05T12:00:00"
    kinds = sorted(s["kind"] + str(s["rule_id"]) for s in iv["sources"])
    assert kinds == ["extra10", "extra11", "weekly1"]


# ---------------------------------------------------------------------------
# DST
# ---------------------------------------------------------------------------


def test_spring_forward_gap_boundaries_shift_to_first_valid_moment():
    # America/New_York 2026-03-08 02:00 -> 03:00（EDT 开始）。
    zone = get_zone("America/New_York")
    d = date(2026, 3, 8)

    # 02:30 落在缺口内，顺延到 03:00 EDT = 07:00 UTC。
    shifted = resolve_local(zone, d, time(2, 30), "earlier")
    assert shifted == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)

    # 02:00（缺口起点本身）也顺延到 07:00 UTC。
    edge = resolve_local(zone, d, time(2, 0), "earlier")
    assert edge == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)

    # 缺口外的时刻不变。
    assert resolve_local(zone, d, time(1, 0), "earlier") == datetime(
        2026, 3, 8, 6, 0, tzinfo=timezone.utc
    )
    assert resolve_local(zone, d, time(3, 0), "later") == datetime(
        2026, 3, 8, 7, 0, tzinfo=timezone.utc
    )


def test_spring_forward_period_inside_gap_becomes_empty():
    zone = get_zone("America/New_York")
    # 周日（2026-03-08 是周日）02:15-02:45 整段落入缺口 => 空区间被丢弃。
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=7, start=time(2, 15), end=time(2, 45))]
    )
    days = build_calendar(zone, rs, d := date(2026, 3, 8), d)
    assert days[0]["intervals"] == []


def test_spring_forward_period_straddling_gap_is_shortened():
    zone = get_zone("America/New_York")
    # 01:30(EST) -> 03:30(EDT)：起点 06:30Z，缺口边界顺延到 07:00Z，终点 07:30Z。
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=7, start=time(1, 30), end=time(3, 30))]
    )
    days = build_calendar(zone, rs, date(2026, 3, 8), date(2026, 3, 8))
    iv = days[0]["intervals"][0]
    assert iv["start_utc"] == datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc)
    assert iv["end_utc"] == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert iv["local_start"] == "2026-03-08T01:30:00"
    assert iv["local_end"] == "2026-03-08T03:30:00"


def test_fall_back_ambiguous_start_earlier_end_later():
    # America/New_York 2026-11-01 02:00 回退到 01:00（EDT -> EST）。
    zone = get_zone("America/New_York")
    d = date(2026, 11, 1)

    # 01:30 重复：earlier = EDT(UTC-4) 05:30Z；later = EST(UTC-5) 06:30Z。
    assert resolve_local(zone, d, time(1, 30), "earlier") == datetime(
        2026, 11, 1, 5, 30, tzinfo=timezone.utc
    )
    assert resolve_local(zone, d, time(1, 30), "later") == datetime(
        2026, 11, 1, 6, 30, tzinfo=timezone.utc
    )

    # 01:00-01:00 风格的“覆盖整个重复小时”常规时段 01:00 -> 01:00 不行，
    # 用 01:30 -> 01:45：两个实例都取到 => 1 小时长。
    rs = RuleSet(
        weekly=[WeeklyRule(id=1, weekday=7, start=time(1, 30), end=time(1, 45))]
    )
    days = build_calendar(zone, rs, d, d)
    iv = days[0]["intervals"][0]
    assert iv["start_utc"] == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    assert iv["end_utc"] == datetime(2026, 11, 1, 6, 45, tzinfo=timezone.utc)
    assert iv["end_utc"] - iv["start_utc"] == timedelta(hours=1, minutes=15)


def test_fall_back_overnight_covers_full_five_hours():
    # 周六 23:30 -> 次日 06:00，跨周日回退夜：物理时长 7 小时（墙上 6.5h + 1h）。
    zone = get_zone("America/New_York")
    rs = RuleSet(
        weekly=[
            WeeklyRule(id=1, weekday=6, start=time(23, 30), end=time(6), end_day_offset=1)
        ]
    )
    days = build_calendar(zone, rs, date(2026, 10, 31), date(2026, 11, 1))
    sat_evening = days[0]["intervals"]
    sun_morning = days[1]["intervals"]
    # 每晚片段与前夜延续是同一个物理区间
    assert sat_evening and sun_morning
    iv = sat_evening[0]
    assert iv["start_utc"] == datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc)
    assert iv["end_utc"] == datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)
    assert iv["end_utc"] - iv["start_utc"] == timedelta(hours=7, minutes=30)
    assert sun_morning[0]["start_utc"] == iv["start_utc"]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def test_invalid_periods_rejected():
    with pytest.raises(Exception):
        WeeklyRule(weekday=8, start=time(9), end=time(10)).validate()
    with pytest.raises(Exception):
        # 当日结束但 end <= start
        WeeklyRule(weekday=1, start=time(10), end=time(9)).validate()
    with pytest.raises(Exception):
        # 超过 24 小时
        WeeklyRule(
            weekday=1, start=time(0), end=time(0, 0, 1), end_day_offset=1
        ).validate()
    with pytest.raises(Exception):
        ExtraRule(day=date(2026, 1, 1), start=time(10), end=time(10)).validate()


def test_unknown_timezone():
    from app.scheduler import UnknownTimeZone

    with pytest.raises(UnknownTimeZone):
        get_zone("Not/AZone")
