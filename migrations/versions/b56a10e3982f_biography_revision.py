"""作者小传新增可追溯的修订关系。

Revision ID: b56a10e3982f
Revises: 9a7f31c2b604
"""
from alembic import op
import sqlalchemy as sa

revision = "b56a10e3982f"
down_revision = "9a7f31c2b604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("author_biographies", sa.Column("revises_biography_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_author_biographies_revises", "author_biographies", "author_biographies",
                          ["revises_biography_id"], ["id"])
    op.create_index("ix_author_biographies_revises_biography_id", "author_biographies", ["revises_biography_id"])


def downgrade() -> None:
    op.drop_index("ix_author_biographies_revises_biography_id", "author_biographies")
    op.drop_constraint("fk_author_biographies_revises", "author_biographies", type_="foreignkey")
    op.drop_column("author_biographies", "revises_biography_id")
