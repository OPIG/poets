"""改用不含姓名与文件位置的作者公开 UUID。

Revision ID: d61a4130e684
Revises: 7f924a0d7a61
"""
from alembic import op

revision = "d61a4130e684"
down_revision = "7f924a0d7a61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """将早期依赖姓名/位置的作者键替换为持久化 UUID；内部 bigint 外键不变。"""
    # 旧键只用于导入定位，来源 ID 和位置仍保存在署名表。
    # 现有 bigint 外键不变。
    op.execute("ALTER TABLE authors RENAME COLUMN identity_key TO public_id")
    op.execute("ALTER TABLE authors ALTER COLUMN public_id TYPE uuid USING gen_random_uuid()")
    op.execute("ALTER TABLE authors ALTER COLUMN public_id SET DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE authors RENAME CONSTRAINT authors_identity_key_key TO authors_public_id_key")


def downgrade() -> None:
    # UUID 无法反推出旧的姓名键，因此不能无损回滚。
    raise NotImplementedError("Opaque author public IDs are deliberately not reversible")
