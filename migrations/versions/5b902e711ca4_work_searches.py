"""为作者、标题与标签建立独立的归一化检索索引。

Revision ID: 5b902e711ca4
Revises: c7d8b142912a
"""
from alembic import op
import sqlalchemy as sa

revision = "5b902e711ca4"
down_revision = "c7d8b142912a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("work_searches",
        sa.Column("work_version_id", sa.BigInteger(), sa.ForeignKey("work_versions.id"), primary_key=True),
        sa.Column("normalized_author", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("normalized_tags", sa.Text(), nullable=False))
    for field in ("author", "title", "tags"):
        op.create_index(f"ix_work_search_{field}_trgm", "work_searches", [f"normalized_{field}"],
            postgresql_using="gin", postgresql_ops={f"normalized_{field}": "gin_trgm_ops"})
    # 旧版本由 reindex 补齐；新版本与原文在同一事务写入。
    # 索引不能与新原文版本脱节。


def downgrade() -> None:
    op.drop_table("work_searches")
