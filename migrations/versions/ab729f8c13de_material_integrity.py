"""Enforce the material discriminator and centralize public eligibility.

Revision ID: ab729f8c13de
Revises: f38a6d192ca0
"""
from alembic import op

revision = "ab729f8c13de"
down_revision = "f38a6d192ca0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为各扩展表检查材料类型，并用 public_materials 集中表达最低公开条件。"""
    op.execute("""
        CREATE FUNCTION enforce_material_type() RETURNS trigger AS $$
        DECLARE expected_kind text; actual_kind text; actual_hash text;
        BEGIN
          expected_kind := CASE TG_TABLE_NAME
            WHEN 'work_versions' THEN 'original'
            WHEN 'author_biographies' THEN 'biography'
            WHEN 'translation_editions' THEN 'translation'
            WHEN 'commentaries' THEN 'commentary'
            WHEN 'pinyin_sets' THEN 'pinyin'
          END;
          SELECT kind, content_hash INTO actual_kind, actual_hash FROM materials WHERE id = NEW.material_id;
          IF actual_kind IS DISTINCT FROM expected_kind THEN
            RAISE EXCEPTION 'Material kind % is invalid for % (expected %)', actual_kind, TG_TABLE_NAME, expected_kind;
          END IF;
          IF TG_TABLE_NAME = 'work_versions' AND actual_hash IS DISTINCT FROM NEW.content_hash THEN
            RAISE EXCEPTION 'Original material hash must match the work version';
          END IF;
          IF TG_TABLE_NAME = 'translation_editions' AND
             (SELECT language_tag FROM materials WHERE id = NEW.material_id) IS DISTINCT FROM NEW.language_tag THEN
            RAISE EXCEPTION 'Translation material and edition languages differ';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    for table in ("work_versions", "author_biographies", "translation_editions", "commentaries", "pinyin_sets"):
        op.execute(f"CREATE TRIGGER check_{table}_material BEFORE INSERT OR UPDATE OF material_id ON {table} FOR EACH ROW EXECUTE FUNCTION enforce_material_type()")
    op.execute("""
        CREATE FUNCTION reject_material_identity_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Material kind and hash are immutable; create a new material';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER immutable_material_identity BEFORE UPDATE OF kind, content_hash
        ON materials FOR EACH ROW EXECUTE FUNCTION reject_material_identity_mutation()
    """)
    op.execute("""
        CREATE VIEW public_materials AS
        SELECT m.id, m.kind, m.language_tag, m.source_id, m.content_hash
        FROM materials m JOIN rights_reviews r ON r.material_id = m.id AND r.is_current
        WHERE m.workflow_status = 'published' AND r.decision = 'approved'
          AND r.reviewed_hash = m.content_hash
          AND nullif(btrim(r.legal_basis), '') IS NOT NULL
          AND nullif(btrim(r.permitted_scope), '') IS NOT NULL
          AND nullif(btrim(r.evidence_uri), '') IS NOT NULL
          AND (r.expires_at IS NULL OR r.expires_at > now())
    """)
    op.execute("""
        CREATE OR REPLACE VIEW public_work_versions AS
        SELECT w.id AS work_id, w.public_id AS work_public_id,
               v.id AS version_id, w.genre, w.author_id, w.original_author_name,
               v.title, v.rhythmic, v.content_text, v.search_text, v.tags
        FROM works w
        JOIN work_versions v ON v.work_id = w.id AND v.is_current
        JOIN public_materials m ON m.id = v.material_id
          AND m.kind = 'original' AND m.content_hash = v.content_hash
        WHERE w.identity_status = 'normal'
    """)


def downgrade() -> None:
    raise NotImplementedError("Material and public eligibility guards are not automatically reversible")
