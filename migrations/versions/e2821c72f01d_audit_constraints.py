"""Separate public work identity and protect editorial relations.

Revision ID: e2821c72f01d
Revises: d61a4130e684
"""
from alembic import op
import sqlalchemy as sa

revision = "e2821c72f01d"
down_revision = "d61a4130e684"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """给作品追加公开 UUID，并约束时间线首选记录、权利证据及跨版本段落对齐。"""
    op.execute("ALTER TABLE works RENAME COLUMN identity_key TO source_lookup_key")
    op.execute("ALTER TABLE works RENAME CONSTRAINT works_identity_key_key TO works_source_lookup_key_key")
    op.execute("ALTER TABLE works ADD COLUMN public_id uuid NOT NULL DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE works ADD CONSTRAINT works_public_id_key UNIQUE (public_id)")
    op.create_index("uq_preferred_work_date", "work_dates", ["work_id"], unique=True,
                    postgresql_where=sa.text("is_preferred"))
    op.execute("""
        ALTER TABLE rights_reviews ADD CONSTRAINT approved_rights_have_evidence
        CHECK (decision <> 'approved' OR
          (nullif(btrim(legal_basis), '') IS NOT NULL AND
           nullif(btrim(permitted_scope), '') IS NOT NULL AND
           nullif(btrim(evidence_uri), '') IS NOT NULL))
    """)
    op.execute("ALTER TABLE work_paragraphs ADD CONSTRAINT nonnegative_paragraph_index CHECK (paragraph_index >= 0)")
    op.execute("ALTER TABLE work_versions ADD CONSTRAINT positive_version_number CHECK (version_number > 0)")
    op.execute("ALTER TABLE author_attributions ADD CONSTRAINT nonnegative_author_source_index CHECK (source_index >= 0)")
    op.execute("ALTER TABLE work_versions ADD CONSTRAINT nonnegative_work_source_index CHECK (source_index >= 0)")
    op.execute("ALTER TABLE translation_blocks ADD CONSTRAINT nonnegative_block_index CHECK (block_index >= 0)")
    op.execute("""
        CREATE FUNCTION enforce_translation_block_alignment() RETURNS trigger AS $$
        DECLARE edition_version bigint; mode text; paragraph_version bigint;
        BEGIN
          SELECT work_version_id, alignment_mode INTO edition_version, mode
            FROM translation_editions WHERE id = NEW.edition_id;
          IF mode = 'whole' AND NEW.work_paragraph_id IS NOT NULL THEN
            RAISE EXCEPTION 'Whole-work translation cannot reference a paragraph';
          ELSIF mode = 'paragraph' THEN
            IF NEW.work_paragraph_id IS NULL THEN
              RAISE EXCEPTION 'Paragraph translation requires a source paragraph';
            END IF;
            SELECT work_version_id INTO paragraph_version FROM work_paragraphs WHERE id = NEW.work_paragraph_id;
            IF paragraph_version IS DISTINCT FROM edition_version THEN
              RAISE EXCEPTION 'Translated paragraph belongs to a different work version';
            END IF;
          ELSIF mode NOT IN ('whole', 'paragraph') THEN
            RAISE EXCEPTION 'Unsupported translation alignment mode';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER check_translation_block_alignment BEFORE INSERT OR UPDATE
        ON translation_blocks FOR EACH ROW EXECUTE FUNCTION enforce_translation_block_alignment()
    """)
    op.execute("""
        CREATE FUNCTION enforce_commentary_paragraph_alignment() RETURNS trigger AS $$
        DECLARE paragraph_version bigint;
        BEGIN
          IF NEW.work_paragraph_id IS NOT NULL THEN
            SELECT work_version_id INTO paragraph_version FROM work_paragraphs WHERE id = NEW.work_paragraph_id;
            IF paragraph_version IS DISTINCT FROM NEW.work_version_id THEN
              RAISE EXCEPTION 'Commentary paragraph belongs to a different work version';
            END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER check_commentary_paragraph_alignment BEFORE INSERT OR UPDATE
        ON commentaries FOR EACH ROW EXECUTE FUNCTION enforce_commentary_paragraph_alignment()
    """)
    op.execute("""
        CREATE FUNCTION enforce_pinyin_alignment() RETURNS trigger AS $$
        DECLARE version_hash text;
        BEGIN
          SELECT content_hash INTO version_hash FROM work_versions WHERE id = NEW.work_version_id;
          IF NEW.alignment_sha256 IS DISTINCT FROM version_hash THEN
            RAISE EXCEPTION 'Pinyin alignment hash differs from source work version';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER check_pinyin_alignment BEFORE INSERT OR UPDATE
        ON pinyin_sets FOR EACH ROW EXECUTE FUNCTION enforce_pinyin_alignment()
    """)
    # A published material must have current, effective evidence for this exact text.
    op.execute("CREATE OR REPLACE VIEW public_work_versions AS SELECT w.id AS work_id, v.id AS version_id, w.genre, w.author_id, w.original_author_name, v.title, v.rhythmic, v.content_text, v.search_text, v.tags FROM works w JOIN work_versions v ON v.work_id = w.id AND v.is_current JOIN materials m ON m.id = v.material_id AND m.workflow_status = 'published' JOIN rights_reviews r ON r.material_id = m.id AND r.is_current AND r.decision = 'approved' AND r.reviewed_hash = m.content_hash AND nullif(btrim(r.legal_basis), '') IS NOT NULL AND nullif(btrim(r.permitted_scope), '') IS NOT NULL AND nullif(btrim(r.evidence_uri), '') IS NOT NULL AND (r.expires_at IS NULL OR r.expires_at > now()) WHERE w.identity_status = 'normal'")


def downgrade() -> None:
    raise NotImplementedError("Public UUID backfill and editorial guards are not automatically reversible")
