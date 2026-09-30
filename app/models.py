"""SQLAlchemy 模型：回收点 + 版本化规则。

每个回收点维护一条 ``published_version`` 指针。规则写入采用整版本快照：
PUT 一次生成一条新的 rule_versions 行及其全部子表行，成功后才翻转指针，
从而保证原子发布；查询始终通过指针读取单个已发布版本。
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# PostgreSQL 下为 BIGINT IDENTITY；SQLite（测试）下退化为 INTEGER PRIMARY KEY
# 以获得 ROWID 自增（SQLite 不会对 BIGINT 主键自动自增）。
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Point(Base):
    __tablename__ = "points"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    # 逻辑外键 -> rule_versions.id；不加数据库外键以避免循环依赖。
    published_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions: Mapped[list["RuleVersion"]] = relationship(
        back_populates="point", passive_deletes=True
    )


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint("point_id", "version", name="uq_version_point_version"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("points.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    point: Mapped[Point] = relationship(back_populates="versions")
    weekly_periods: Mapped[list["WeeklyPeriod"]] = relationship(
        back_populates="rule_version",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )
    closures: Mapped[list["Closure"]] = relationship(
        back_populates="rule_version",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )
    extra_openings: Mapped[list["ExtraOpening"]] = relationship(
        back_populates="rule_version",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )


class WeeklyPeriod(Base):
    __tablename__ = "weekly_periods"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_weekly_weekday"),
        CheckConstraint("end_day_offset IN (0, 1)", name="ck_weekly_offset"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    rule_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rule_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_day_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    rule_version: Mapped[RuleVersion] = relationship(
        back_populates="weekly_periods"
    )


class Closure(Base):
    __tablename__ = "closures"
    __table_args__ = (
        UniqueConstraint(
            "rule_version_id", "day", name="uq_closure_version_day"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    rule_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rule_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule_version: Mapped[RuleVersion] = relationship(back_populates="closures")


class ExtraOpening(Base):
    __tablename__ = "extra_openings"
    __table_args__ = (
        UniqueConstraint(
            "rule_version_id", "day", "start_time", "end_time",
            name="uq_extra_version_day_time",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    rule_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rule_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    rule_version: Mapped[RuleVersion] = relationship(
        back_populates="extra_openings"
    )
