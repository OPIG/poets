"""修正内容触发器访问不存在字段的问题。

Revision ID: c7d8b142912a
Revises: ab729f8c13de
"""
from alembic import op

revision = "c7d8b142912a"
down_revision = "ab729f8c13de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """修复旧触发器在其他内容表上访问不存在字段的问题；每种表只校验自身拥有的列。"""
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_material_type() RETURNS trigger AS $$
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
          IF TG_TABLE_NAME = 'work_versions' THEN
            IF actual_hash IS DISTINCT FROM NEW.content_hash THEN
              RAISE EXCEPTION 'Original material hash must match the work version';
            END IF;
          END IF;
          IF TG_TABLE_NAME = 'translation_editions' THEN
            IF (SELECT language_tag FROM materials WHERE id = NEW.material_id) IS DISTINCT FROM NEW.language_tag THEN
              RAISE EXCEPTION 'Translation material and edition languages differ';
            END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    raise NotImplementedError("Material integrity guard cannot be automatically relaxed")
