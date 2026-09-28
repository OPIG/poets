"""Alembic 运行入口：在线连接数据库，离线生成 SQL 时共享 ORM 元数据。"""
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from poets_catalog.db import database_url
from poets_catalog.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
# 环境变量优先于 alembic.ini，使迁移与导入脚本绝不会连接不同数据库。
config.set_main_option("sqlalchemy.url", database_url())
target_metadata = Base.metadata


def run_migrations_offline():
    """不建立连接时输出迁移 SQL，适合审阅或由外部部署工具执行。"""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """在事务中执行迁移；失败由 PostgreSQL 回滚该版本 DDL。"""
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
