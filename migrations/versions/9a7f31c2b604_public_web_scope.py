"""公开视图仅接受明确允许本站网页展示的审核范围。

Revision ID: 9a7f31c2b604
Revises: 74c4a9e86f20
"""
from alembic import op

revision = "9a7f31c2b604"
down_revision = "74c4a9e86f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE VIEW public_materials AS
        SELECT m.id, m.kind, m.language_tag, m.source_id, m.content_hash
        FROM materials m JOIN rights_reviews r ON r.material_id = m.id AND r.is_current
        WHERE m.workflow_status = 'published' AND r.decision = 'approved'
          AND r.reviewed_hash = m.content_hash
          AND nullif(btrim(r.legal_basis), '') IS NOT NULL
          AND r.permitted_scope = 'web'
          AND nullif(btrim(r.evidence_uri), '') IS NOT NULL
          AND (r.expires_at IS NULL OR r.expires_at > now())
    """)


def downgrade() -> None:
    raise NotImplementedError("公开权限范围不应自动放宽")
