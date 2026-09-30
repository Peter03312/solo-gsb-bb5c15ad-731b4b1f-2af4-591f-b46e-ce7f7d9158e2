"""HTTP API 集成测试（SQLite 内存库）。"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest


def _iso_utc(s: str) -> datetime:
    # 兼容序列化输出的 Z 后缀。
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_full_publish_query_versioning_flow(client):
    # 1) 建点
    r = await client.post(
        "/points",
        json={
            "code": "t-point",
            "name": "测试回收点",
            "timezone": "Asia/Shanghai",
        },
    )
    assert r.status_code == 201, r.text
    point = r.json()
    pid = point["id"]
    assert point["published_version"] == 0

    # 2) 未发布时查询日历/规则 => 409 no_published_rules
    r = await client.get(f"/points/{pid}/calendar?from=2026-10-01&to=2026-10-02")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "no_published_rules"

    r = await client.get(f"/points/{pid}/rules")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "no_published_rules"

    # 3) 首次发布（base_version=0）
    body = {
        "base_version": 0,
        "note": "v1",
        "weekly_periods": [
            {"weekday": 1, "start": "09:00", "end": "12:00"},
            {"weekday": 6, "start": "20:00", "end": "02:00", "end_day_offset": 1},
        ],
        "closures": [{"day": "2026-10-02", "reason": "停开"}],
        "extra_openings": [
            {"day": "2026-10-02", "start": "14:00", "end": "17:00"}
        ],
    }
    r = await client.put(f"/points/{pid}/rules", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1

    # 4) 版本冲突：仍用 base_version=0 再次提交
    r = await client.put(f"/points/{pid}/rules", json=body)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "version_conflict"
    assert err["details"]["current_version"] == 1
    assert err["details"]["base_version"] == 0

    # 5) 版本号递增发布 v2
    body2 = dict(body, base_version=1, note="v2")
    r = await client.put(f"/points/{pid}/rules", json=body2)
    assert r.status_code == 201
    assert r.json()["version"] == 2

    # 6) 查询当前已发布版本与历史版本
    r = await client.get(f"/points/{pid}/rules")
    assert r.status_code == 200
    assert r.json()["version"] == 2
    r = await client.get(f"/points/{pid}/versions")
    assert [v["version"] for v in r.json()] == [1, 2]
    r = await client.get(f"/points/{pid}/versions/1")
    assert r.json()["note"] == "v1"

    # 7) 日历查询：2026-10-01 周四无规则；10-02 周五停开但有补开
    r = await client.get(f"/points/{pid}/calendar?from=2026-10-01&to=2026-10-03")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 2
    assert data["timezone"] == "Asia/Shanghai"
    days = {d["local_date"]: d for d in data["days"]}
    assert days["2026-10-01"]["intervals"] == []
    fri = days["2026-10-02"]
    assert fri["closed"] is True
    assert len(fri["intervals"]) == 1
    iv = fri["intervals"][0]
    assert iv["start_utc"].endswith(("+00:00", "Z"))
    assert _iso_utc(iv["start_utc"]) == datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
    assert _iso_utc(iv["end_utc"]) == datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    assert iv["sources"][0]["kind"] == "extra"
    # 周六当晚跨午夜片段
    sat = days["2026-10-03"]
    assert sat["intervals"][0]["local_start"] == "2026-10-03T20:00:00"
    assert sat["intervals"][0]["local_end"] == "2026-10-04T02:00:00"


@pytest.mark.asyncio
async def test_invalid_periods_are_distinguishable(client):
    r = await client.post(
        "/points",
        json={"code": "tzx", "name": "x", "timezone": "Asia/Shanghai"},
    )
    pid = r.json()["id"]

    # 当日结束但 end <= start
    r = await client.put(
        f"/points/{pid}/rules",
        json={
            "base_version": 0,
            "weekly_periods": [{"weekday": 1, "start": "18:00", "end": "09:00"}],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_period"

    # 超过 24 小时
    r = await client.put(
        f"/points/{pid}/rules",
        json={
            "base_version": 0,
            "weekly_periods": [
                {"weekday": 1, "start": "00:00", "end": "00:00:01",
                 "end_day_offset": 1}
            ],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_period"

    # 补开 end <= start
    r = await client.put(
        f"/points/{pid}/rules",
        json={
            "base_version": 0,
            "extra_openings": [
                {"day": "2026-10-01", "start": "10:00", "end": "10:00"}
            ],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_period"


@pytest.mark.asyncio
async def test_unknown_timezone_rejected(client):
    r = await client.post(
        "/points",
        json={"code": "bad", "name": "bad", "timezone": "Mars/Olympus"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_timezone"


@pytest.mark.asyncio
async def test_malformed_body_is_invalid_request(client):
    r = await client.post(
        "/points", json={"code": "z", "timezone": "Asia/Shanghai"}
    )  # 缺 name
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_point_not_found_and_bad_range(client):
    r = await client.get("/points/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "point_not_found"

    r = await client.post(
        "/points",
        json={"code": "rng", "name": "r", "timezone": "Asia/Shanghai"},
    )
    pid = r.json()["id"]
    r = await client.put(
        f"/points/{pid}/rules",
        json={"base_version": 0,
              "weekly_periods": [{"weekday": 1, "start": "09:00", "end": "10:00"}]},
    )
    assert r.status_code == 201

    r = await client.get(f"/points/{pid}/calendar?from=2026-10-10&to=2026-10-01")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_dst_spring_forward_end_to_end(client):
    r = await client.post(
        "/points",
        json={"code": "ny", "name": "NY", "timezone": "America/New_York"},
    )
    pid = r.json()["id"]
    # 2026-03-08 是周日；01:30 -> 03:30 跨越 02:00->03:00 缺口。
    r = await client.put(
        f"/points/{pid}/rules",
        json={
            "base_version": 0,
            "weekly_periods": [
                {"weekday": 7, "start": "01:30", "end": "03:30"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    r = await client.get(f"/points/{pid}/calendar?from=2026-03-08&to=2026-03-08")
    iv = r.json()["days"][0]["intervals"]
    assert _iso_utc(iv[0]["start_utc"]) == datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc)
    assert _iso_utc(iv[0]["end_utc"]) == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
