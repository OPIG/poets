"""只展示已通过版权审核的作品视图。

Revision ID: 4a1d56a72e38
Revises: ba1c8e96da0d
"""
from alembic import op

revision = "4a1d56a72e38"
down_revision = "ba1c8e96da0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """第一版公开作品视图；原始表仍包含私有内容，不能直接授予前端查询权限。"""
    op.execute("""
        CREATE VIEW public_work_versions AS
        SELECT w.id AS work_id, v.id AS version_id, w.genre, w.author_id,
               w.original_author_name, v.title, v.rhythmic, v.content_text,
               v.search_text, v.tags
        FROM works w
        JOIN work_versions v ON v.work_id = w.id AND v.is_current
        JOIN materials m ON m.id = v.material_id AND m.workflow_status = 'published'
        JOIN rights_reviews r ON r.material_id = m.id AND r.is_current
          AND r.decision = 'approved' AND r.reviewed_hash = m.content_hash
          AND r.permitted_scope IS NOT NULL
          AND (r.expires_at IS NULL OR r.expires_at > now())
        WHERE w.identity_status = 'normal'
    """)


def downgrade() -> None:
    op.execute("DROP VIEW public_work_versions")
