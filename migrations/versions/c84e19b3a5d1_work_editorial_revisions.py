"""作品扩展材料的修订关系。

Revision ID: c84e19b3a5d1
Revises: b56a10e3982f
"""
from alembic import op
import sqlalchemy as sa

revision = "c84e19b3a5d1"
down_revision = "b56a10e3982f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("commentaries", "translation_editions", "pinyin_sets"):
        op.add_column(table, sa.Column("revises_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(f"fk_{table}_revises", table, table, ["revises_id"], ["id"])
        op.create_index(f"ix_{table}_revises_id", table, ["revises_id"])


def downgrade() -> None:
    for table in ("commentaries", "translation_editions", "pinyin_sets"):
        op.drop_index(f"ix_{table}_revises_id", table)
        op.drop_constraint(f"fk_{table}_revises", table, type_="foreignkey")
        op.drop_column(table, "revises_id")
