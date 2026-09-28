"""刷新页面后恢复会话的 CSRF 随机值。

Revision ID: 74c4a9e86f20
Revises: 8b3ef7d24091
"""
from alembic import op
import sqlalchemy as sa

revision = "74c4a9e86f20"
down_revision = "8b3ef7d24091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧行只有摘要，不能恢复原始 CSRF 值；使旧会话失效后再替换列。
    op.execute("UPDATE admin_sessions SET revoked_at = now() WHERE revoked_at IS NULL")
    op.drop_column("admin_sessions", "csrf_hash")
    op.add_column("admin_sessions", sa.Column("csrf_token", sa.String(80), nullable=True))
    op.execute("UPDATE admin_sessions SET csrf_token = '' WHERE csrf_token IS NULL")
    op.alter_column("admin_sessions", "csrf_token", nullable=False)


def downgrade() -> None:
    raise NotImplementedError("不能从会话 CSRF 明文还原旧列的历史摘要")
