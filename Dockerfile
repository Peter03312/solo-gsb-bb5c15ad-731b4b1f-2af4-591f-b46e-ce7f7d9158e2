FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# psycopg[binary] 自带 libpq，无需系统编译依赖。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

# 启动时自动建表并写入演示数据（可用 SEED_DEMO=0 关闭）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
