"""Keep versioned source text and alignment parents stable.

Revision ID: f38a6d192ca0
Revises: e2821c72f01d
"""
from alembic import op

revision = "f38a6d192ca0"
down_revision = "e2821c72f01d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """原文版本与段落不可原地改写；译本有段落后不可随意换原文版本。"""
    op.execute("""
        CREATE FUNCTION reject_source_text_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Imported source text is immutable; create a new work version';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER immutable_work_text BEFORE UPDATE OF
          source_id, source_path, source_index, original_id, title, rhythmic,
          prologue, tags, raw_payload, content_text, search_text, content_hash,
          version_number, work_id, material_id
        ON work_versions FOR EACH ROW EXECUTE FUNCTION reject_source_text_mutation()
    """)
    op.execute("""
        CREATE TRIGGER immutable_work_paragraph BEFORE UPDATE OF
          work_version_id, paragraph_index, body
        ON work_paragraphs FOR EACH ROW EXECUTE FUNCTION reject_source_text_mutation()
    """)
    op.execute("""
        CREATE FUNCTION reject_translation_parent_move() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM translation_blocks WHERE edition_id = OLD.id) THEN
            RAISE EXCEPTION 'Translation edition has blocks; create a new edition for another version or mode';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER fixed_translation_parent BEFORE UPDATE OF
          work_version_id, alignment_mode, material_id
        ON translation_editions FOR EACH ROW EXECUTE FUNCTION reject_translation_parent_move()
    """)
    op.execute("DROP VIEW public_work_versions")
    op.execute("""
        CREATE VIEW public_work_versions AS
        SELECT w.id AS work_id, w.public_id AS work_public_id,
               v.id AS version_id, w.genre, w.author_id, w.original_author_name,
               v.title, v.rhythmic, v.content_text, v.search_text, v.tags
        FROM works w
        JOIN work_versions v ON v.work_id = w.id AND v.is_current
        JOIN materials m ON m.id = v.material_id
          AND m.kind = 'original' AND m.content_hash = v.content_hash
          AND m.workflow_status = 'published'
        JOIN rights_reviews r ON r.material_id = m.id AND r.is_current
          AND r.decision = 'approved' AND r.reviewed_hash = m.content_hash
          AND nullif(btrim(r.legal_basis), '') IS NOT NULL
          AND nullif(btrim(r.permitted_scope), '') IS NOT NULL
          AND nullif(btrim(r.evidence_uri), '') IS NOT NULL
          AND (r.expires_at IS NULL OR r.expires_at > now())
        WHERE w.identity_status = 'normal'
    """)


def downgrade() -> None:
    raise NotImplementedError("Editorial immutability and public view policy are not automatically reversible")
