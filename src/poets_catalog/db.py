"""集中管理 PostgreSQL 连接，导入器与 Alembic 使用同一 DATABASE_URL。"""
import os
from sqlalchemy import create_engine

# 不设置环境变量时使用本机 Unix socket 与当前系统用户名连接开发库。
DEFAULT_URL = "postgresql+psycopg:///poets_dev"


def database_url() -> str:
    """读取连接串；生产/测试环境应显式设置 DATABASE_URL。"""
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def engine():
    """为一次 CLI 操作创建连接池，调用方结束时负责 dispose。"""
    return create_engine(database_url(), pool_pre_ping=True)
