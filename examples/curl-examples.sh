#!/usr/bin/env bash
# 社区回收点服务日历 API —— 端到端调用示例。
# 依赖：curl、jq（可选；无 jq 时自动退化为原始输出）。
set -euo pipefail

BASE="${BASE_URL:-http://localhost:8080}"

if command -v jq >/dev/null 2>&1; then
  j() { jq .; }
else
  j() { cat; }
fi

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

say "1) 健康检查"
curl -sS "$BASE/health" | j

say "2) 列出回收点（演示数据已内置两个点）"
curl -sS "$BASE/points" | j

say "3) 新建回收点（IANA 时区）"
POINT=$(curl -sS -X POST "$BASE/points" \
  -H 'Content-Type: application/json' \
  -d '{"code":"demo-point","name":"示例社区回收点","timezone":"Asia/Shanghai"}')
echo "$POINT" | j
PID=$(echo "$POINT" | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)
echo "point id = $PID"

say "4) 首次发布规则 v1（base_version=0）"
# 周一/三/五 09:00-12:00；周六 20:00-次日02:00（跨午夜）
# 2026-10-02 全天停开，当日 14:00-17:00 补开
curl -sS -X PUT "$BASE/points/$PID/rules" \
  -H 'Content-Type: application/json' \
  -d '{
    "base_version": 0,
    "note": "初始规则",
    "weekly_periods": [
      {"weekday": 1, "start": "09:00", "end": "12:00"},
      {"weekday": 3, "start": "09:00", "end": "12:00"},
      {"weekday": 5, "start": "09:00", "end": "12:00"},
      {"weekday": 6, "start": "20:00", "end": "02:00", "end_day_offset": 1}
    ],
    "closures": [{"day": "2026-10-02", "reason": "设备检修"}],
    "extra_openings": [{"day": "2026-10-02", "start": "14:00", "end": "17:00"}]
  }' | j

say "5) 重复提交同一 base_version -> 409 version_conflict"
curl -sS -o /tmp/conflict.json -w "HTTP %{http_code}\n" -X PUT "$BASE/points/$PID/rules" \
  -H 'Content-Type: application/json' \
  -d '{"base_version": 0, "weekly_periods": []}'
cat /tmp/conflict.json | j || true

say "6) 查询日历（当地日期两端包含：2026-10-01 .. 2026-10-04）"
curl -sS "$BASE/points/$PID/calendar?from=2026-10-01&to=2026-10-04" | j

say "7) 读取当前已发布版本 / 历史版本列表"
curl -sS "$BASE/points/$PID/rules" | j
curl -sS "$BASE/points/$PID/versions" | j

say "8) 非法时段 -> 422 invalid_period（当日结束却 end<=start）"
curl -sS -o /tmp/inv.json -w "HTTP %{http_code}\n" -X PUT "$BASE/points/$PID/rules" \
  -H 'Content-Type: application/json' \
  -d '{"base_version": 1, "weekly_periods": [{"weekday": 1, "start": "18:00", "end": "09:00"}]}'
cat /tmp/inv.json | j || true

say "9) 未知时区 -> 422 unknown_timezone"
curl -sS -o /tmp/tz.json -w "HTTP %{http_code}\n" -X POST "$BASE/points" \
  -H 'Content-Type: application/json' \
  -d '{"code":"bad","name":"bad","timezone":"Mars/Olympus"}'
cat /tmp/tz.json | j || true

say "10) 无已发布规则 -> 409 no_published_rules"
RAW=$(curl -sS -X POST "$BASE/points" -H 'Content-Type: application/json' \
  -d '{"code":"empty-point","name":"空","timezone":"UTC"}')
EMPTY=$(echo "$RAW" | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)
curl -sS -o /tmp/none.json -w "HTTP %{http_code}\n" \
  "$BASE/points/$EMPTY/calendar?from=2026-10-01&to=2026-10-01"
cat /tmp/none.json | j || true

echo
echo "完成。API 文档：$BASE/docs （Swagger UI） / $BASE/openapi.json"
