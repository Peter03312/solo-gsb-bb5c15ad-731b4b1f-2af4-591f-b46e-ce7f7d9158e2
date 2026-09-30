"""Python 调用示例（标准库 urllib，零依赖）。

运行：BASE_URL=http://localhost:8080 python examples/python_examples.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def show(title: str, status: int, payload: object) -> None:
    print(f"\n== {title} == [{status}]")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    show("health", *call("GET", "/health"))

    status, point = call(
        "POST", "/points",
        {"code": "py-demo", "name": "Python 示例回收点",
         "timezone": "America/New_York"},
    )
    show("create point", status, point)
    pid = point["id"]

    # 春季跳时演示：2026-03-08 周日 01:30-03:30（02:00-03:00 缺口被跳过）
    rules = {
        "base_version": 0,
        "note": "spring-forward demo",
        "weekly_periods": [
            {"weekday": 7, "start": "01:30", "end": "03:30"},
            {"weekday": 6, "start": "23:30", "end": "06:00",
             "end_day_offset": 1},
        ],
        "closures": [{"day": "2026-11-26", "reason": "Thanksgiving"}],
        "extra_openings": [],
    }
    show("publish v1", *call("PUT", f"/points/{pid}/rules", rules))

    # 版本冲突
    show("republish with stale base_version",
         *call("PUT", f"/points/{pid}/rules", rules))

    qs = urllib.parse.urlencode({"from": "2026-03-07", "to": "2026-03-08"})
    show("calendar around spring-forward",
         *call("GET", f"/points/{pid}/calendar?{qs}"))

    qs = urllib.parse.urlencode({"from": "2026-10-31", "to": "2026-11-01"})
    show("calendar around fall-back",
         *call("GET", f"/points/{pid}/calendar?{qs}"))


if __name__ == "__main__":
    main()
