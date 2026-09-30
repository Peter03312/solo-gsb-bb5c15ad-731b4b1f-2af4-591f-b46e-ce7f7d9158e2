# 社区回收点服务日历 HTTP API

使用 **Python 3.11 + FastAPI + PostgreSQL** 实现的回收点服务日历。

- 维护回收点的 **IANA 时区**、**每周开放时段**、**指定日期全天停开** 与 **当日补开时段**。
- 常规时段支持跨午夜（最长 24 小时），跨午夜时段按 **开始日** 匹配星期规则。
- 按 **两端包含的当地日期范围** 查询，逐日裁切并返回实际开放区间的 **UTC 起止**、**当地日期** 与 **来源**。
- 停开日扣除该日全部常规片段（含前夜延续），再叠加补开；重叠区间合并并保留所有来源。
- **夏令时**：缺失的墙上时间顺延到首个有效时刻；重复时间起点取较早实例、终点取较晚实例。
- 规则以 **整版本快照 + 乐观版本号** 条件提交并原子发布；查询始终基于单个已发布版本。
- 无效时段、未知时区、无已发布规则、版本冲突分别返回可区分的错误码。

## 一键启动（Docker Compose）

需要 Docker 与 Docker Compose v2。

```bash
docker compose up --build
```

启动后：

- API：<http://localhost:8080>
- 交互式文档（Swagger UI）：<http://localhost:8080/docs>
- OpenAPI：<http://localhost:8080/openapi.json>
- 健康检查：<http://localhost:8080/health>
- PostgreSQL：`localhost:5432`（库/用户/密码均为 `recycle`）

首次启动会自动建表，并写入两个演示回收点（`SEED_DEMO=1`）：

| code | 时区 | 说明 |
| --- | --- | --- |
| `lake-garden` | `Asia/Shanghai` | 周一/三/五 09–12，周六 20–次日 02；2026-10-01 停开+下午补开 |
| `brooklyn-center` | `America/New_York` | 跨午夜 + 周四凌晨时段，覆盖春/秋季 DST 边界 |

停止与清理：

```bash
docker compose down          # 停止，保留数据
docker compose down -v       # 同时删除数据库卷
```

### 不使用容器（本地运行）

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://recycle:recycle@localhost:5432/recycle"
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://recycle:recycle@db:5432/recycle` | 数据库连接串（也接受 `postgresql://`、`postgres://` 前缀） |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8080` | 监听端口 |
| `SEED_DEMO` | `1` | 启动时幂等写入演示回收点 |
| `MAX_RANGE_DAYS` | `366` | 单次日历查询允许的最大日期跨度 |

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/points` | 创建回收点（code/name/IANA timezone） |
| GET | `/points` | 回收点列表 |
| GET | `/points/{id}` | 回收点详情（含当前已发布版本号） |
| PUT | `/points/{id}/rules` | **按版本号条件原子发布**整套规则 |
| GET | `/points/{id}/rules` | 读取当前已发布版本快照 |
| GET | `/points/{id}/versions` | 历史版本列表 |
| GET | `/points/{id}/versions/{n}` | 指定历史版本 |
| GET | `/points/{id}/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD` | 日历查询（两端包含） |
| GET | `/health` | 健康检查 |

### 规则模型

```jsonc
{
  "base_version": 0,            // 必须等于当前已发布版本号；首次发布传 0
  "note": "可选备注",
  "weekly_periods": [           // 每周固定时段
    {"weekday": 1, "start": "09:00", "end": "12:00"},
    {"weekday": 6, "start": "20:00", "end": "02:00", "end_day_offset": 1}
  ],
  "closures": [                 // 全天停开
    {"day": "2026-10-02", "reason": "设备检修"}
  ],
  "extra_openings": [           // 当日补开（不跨日，停开日也生效）
    {"day": "2026-10-02", "start": "14:00", "end": "17:00"}
  ]
}
```

- `weekday`：ISO 星期，`1=周一 … 7=周日`。
- `end_day_offset`：`0` 当日结束（要求 `end > start`）；`1` 次日结束，即跨午夜（墙上时长 ≤ 24h）。
- 每次 PUT 都是 **全量快照替换**：未出现在请求中的旧规则不会保留。
- 服务端在单个事务中校验并写入新版本（PostgreSQL 使用 `SELECT … FOR UPDATE` + `REPEATABLE READ`，`(point_id, version)` 上有唯一约束），全部成功才翻转发布指针。

### 日历响应

```jsonc
{
  "point_id": 1,
  "timezone": "Asia/Shanghai",
  "version": 1,                 // 本次查询所基于的已发布版本
  "date_from": "2026-10-01",
  "date_to": "2026-10-04",
  "days": [
    {
      "local_date": "2026-10-03",
      "closed": false,
      "intervals": [
        {
          "start_utc": "2026-10-03T12:00:00Z",
          "end_utc":   "2026-10-03T18:00:00Z",
          "local_start": "2026-10-03T20:00:00",
          "local_end":   "2026-10-04T02:00:00",
          "sources": [
            {"kind": "weekly", "rule_id": 4, "weekday": 6,
             "day": "2026-10-03", "fragment": "evening"}
          ]
        }
      ]
    }
  ]
}
```

`sources.fragment` 取值：

- `full`：当日起止的常规时段；
- `evening`：开始于当天的跨午夜时段（当晚部分）；
- `morning`：开始于前一天的跨午夜时段在当日凌晨的延续（同一物理区间会同时出现在开始日和结束日的逐日视图中）；
- `kind=extra`：当日补开。

多个来源重叠时区间会合并，`sources` 保留全部来源。

## 排程与夏令时语义

- **逐日裁切**：对每个当地日期 `d`，片段来自 ① 开始于 `d` 的常规时段；② 开始于 `d-1` 的跨午夜延续；③ `d` 的补开。
- **停开**：`d` 停开时扣除与 `d` 相关的全部常规片段——开始于 `d` 的当晚片段（及其向 `d+1` 的延续）和开始于 `d-1` 的前夜延续；补开仍叠加。
- **春季跳时（缺口）**：落在缺口内的边界顺延到缺口结束后的首个有效时刻（如纽约 02:30 → 03:00 EDT）。整个时段都落入缺口则该片段为空被丢弃。
- **秋季回退（重复）**：起点取较早实例（fold=0，UTC−4），终点取较晚实例（fold=1，UTC−5），因此覆盖重复小时的时段物理时长为“墙上时长 + 1 小时”。

## 错误响应与状态码

统一结构：`{"error": {"code": "...", "message": "...", "details": ...}}`

| HTTP | code | 触发场景 |
| --- | --- | --- |
| 400 | `invalid_request` | 请求体格式错误、日期范围倒置或过大 |
| 404 | `point_not_found` | 回收点（或指定历史版本）不存在 |
| 409 | `no_published_rules` | 回收点尚未发布任何规则 |
| 409 | `version_conflict` | `base_version` 与当前已发布版本不一致 |
| 422 | `invalid_period` | 时段非法：星期越界、当日 `end<=start`、时长 ≤0 或 >24h、补开 `end<=start`、重复条目 |
| 422 | `unknown_timezone` | 不是合法 IANA 时区名 |

## 调用示例

```bash
# 启动后运行完整示例
bash examples/curl-examples.sh

# 或 Python（仅标准库）
python examples/python_examples.py
```

最小调用：

```bash
curl -X POST localhost:8080/points -H 'Content-Type: application/json' \
  -d '{"code":"p1","name":"一号点","timezone":"Asia/Shanghai"}'

curl -X PUT localhost:8080/points/1/rules -H 'Content-Type: application/json' -d '{
  "base_version": 0,
  "weekly_periods": [
    {"weekday": 1, "start": "09:00", "end": "12:00"},
    {"weekday": 6, "start": "20:00", "end": "02:00", "end_day_offset": 1}
  ],
  "closures": [{"day": "2026-10-02", "reason": "检修"}],
  "extra_openings": [{"day": "2026-10-02", "start": "14:00", "end": "17:00"}]
}'

curl "localhost:8080/points/1/calendar?from=2026-10-01&to=2026-10-04"
```

## 测试

```bash
pip install -r requirements.txt
pytest -q
```

测试覆盖：跨午夜按开始日匹配、停开扣除（含前夜延续）、停开日补开、重叠合并与来源保留、春季缺口顺延/空段/缩段、秋季重复起点较早/终点较晚、跨回退夜时长，以及发布、乐观锁冲突、各类可区分错误的端到端 HTTP 流程。
