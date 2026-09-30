"""Pydantic 请求 / 响应模型。"""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 回收点
# ---------------------------------------------------------------------------


class PointCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, examples=["lake-garden"])
    name: str = Field(..., min_length=1, max_length=200)
    timezone: str = Field(..., min_length=1, max_length=64, examples=["Asia/Shanghai"])


class PointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    timezone: str
    published_version: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 规则（整版本快照）
# ---------------------------------------------------------------------------


class WeeklyPeriodIn(BaseModel):
    # ISO 星期：1=周一 … 7=周日
    weekday: int = Field(..., ge=1, le=7)
    start: time
    end: time
    # 1 表示结束在次日（跨午夜，end 允许为 00:xx；最长 24 小时）
    end_day_offset: int = Field(0, ge=0, le=1)


class ClosureIn(BaseModel):
    day: date
    reason: str | None = None


class ExtraOpeningIn(BaseModel):
    day: date
    start: time
    end: time


class RuleSetIn(BaseModel):
    # 乐观锁：必须等于当前已发布版本号；首次发布传 0。
    base_version: int = Field(..., ge=0)
    note: str | None = None
    weekly_periods: list[WeeklyPeriodIn] = Field(default_factory=list)
    closures: list[ClosureIn] = Field(default_factory=list)
    extra_openings: list[ExtraOpeningIn] = Field(default_factory=list)


class WeeklyPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weekday: int
    start: time = Field(validation_alias="start_time")
    end: time = Field(validation_alias="end_time")
    end_day_offset: int = 0


class ClosureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    day: date
    reason: str | None = None


class ExtraOpeningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    day: date
    start: time = Field(validation_alias="start_time")
    end: time = Field(validation_alias="end_time")


class RuleVersionOut(BaseModel):
    point_id: int
    version: int
    note: str | None
    created_at: datetime
    weekly_periods: list[WeeklyPeriodOut]
    closures: list[ClosureOut]
    extra_openings: list[ExtraOpeningOut]


class PublishResult(BaseModel):
    point_id: int
    version: int
    note: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# 日历查询结果
# ---------------------------------------------------------------------------


class SourceOut(BaseModel):
    kind: str
    rule_id: int | None
    weekday: int | None = None
    day: date | None = None
    fragment: str | None = None


class IntervalOut(BaseModel):
    start_utc: datetime
    end_utc: datetime
    local_start: str
    local_end: str
    sources: list[SourceOut]


class CalendarDay(BaseModel):
    local_date: date
    closed: bool
    intervals: list[IntervalOut]


class CalendarOut(BaseModel):
    point_id: int
    timezone: str
    version: int
    date_from: date
    date_to: date
    days: list[CalendarDay]
