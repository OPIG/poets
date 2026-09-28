"""Distinguish canonical CI authors from source positions.

Revision ID: 7f924a0d7a61
Revises: 4a1d56a72e38
"""
from alembic import op

revision = "7f924a0d7a61"
down_revision = "4a1d56a72e38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """修复早期把宋词作者文件位置误当人物身份的历史数据：只移动未公开且无人工关联的歧义条目。原署名和简介仍保留在 author_attributions。"""
    # A repeated name without a source ID is a source attribution, not a person.
    op.execute("""
        CREATE TEMP TABLE ci_ambiguous_author_ids ON COMMIT DROP AS
        SELECT DISTINCT a.author_id AS id
        FROM author_attributions a
        JOIN (
            SELECT source_id, original_name FROM author_attributions
            WHERE source_path = '宋词/author.song.json'
            GROUP BY source_id, original_name HAVING count(*) > 1
        ) d USING (source_id, original_name)
        WHERE a.source_path = '宋词/author.song.json' AND a.author_id IS NOT NULL
    """)
    # Refuse to unlink manually enriched or approved records.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM works WHERE author_id IN (SELECT id FROM ci_ambiguous_author_ids))
             OR EXISTS (SELECT 1 FROM author_events WHERE author_id IN (SELECT id FROM ci_ambiguous_author_ids))
             OR EXISTS (
                SELECT 1 FROM author_biographies b JOIN materials m ON m.id = b.material_id
                WHERE b.author_id IN (SELECT id FROM ci_ambiguous_author_ids)
                  AND (m.workflow_status <> 'staged' OR m.origin_type <> 'imported'
                       OR EXISTS (SELECT 1 FROM rights_reviews r WHERE r.material_id = m.id))
             )
          THEN RAISE EXCEPTION 'Ambiguous CI authors have curated associations; resolve manually before migration';
          END IF;
        END $$
    """)
    op.execute("""
        UPDATE author_attributions a SET author_id = NULL, match_status = 'ambiguous'
        WHERE a.author_id IN (SELECT id FROM ci_ambiguous_author_ids)
          AND a.source_path = '宋词/author.song.json'
    """)
    op.execute("""
        CREATE TEMP TABLE ci_orphan_bio_materials ON COMMIT DROP AS
        SELECT material_id AS id FROM author_biographies
        WHERE author_id IN (SELECT id FROM ci_ambiguous_author_ids)
    """)
    op.execute("DELETE FROM author_biographies WHERE author_id IN (SELECT id FROM ci_ambiguous_author_ids)")
    op.execute("DELETE FROM materials WHERE id IN (SELECT id FROM ci_orphan_bio_materials)")
    op.execute("DELETE FROM authors WHERE id IN (SELECT id FROM ci_ambiguous_author_ids)")
    # For unambiguous profiles the name is a stable source-local identity; the
    # file path and offset remain exclusively in author_attributions.
    op.execute("""
        UPDATE authors a SET identity_key = 'author:ci:' || a.canonical_name
        WHERE a.identity_key LIKE 'author:ci:宋词/author.song.json:%'
    """)


def downgrade() -> None:
    # Historical source positions are deliberately not restored as person IDs.
    pass
