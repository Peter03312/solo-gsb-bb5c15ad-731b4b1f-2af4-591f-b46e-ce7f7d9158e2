"""运行配置（全部通过环境变量覆盖）。"""

from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://recycle:recycle@db:5432/recycle",
        )
        self.app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port: int = int(os.getenv("APP_PORT", "8080"))
        # 启动时是否写入演示回收点（幂等，已存在则跳过）。
        self.seed_demo: bool = os.getenv("SEED_DEMO", "1") == "1"
        # 单次日历查询允许的最大天数，防止超大范围扫描。
        self.max_range_days: int = int(os.getenv("MAX_RANGE_DAYS", "366"))


settings = Settings()
