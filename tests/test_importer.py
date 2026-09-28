from pathlib import Path
import os
import pytest
from sqlalchemy import select, func, text, update, insert
from poets_catalog.db import engine
from poets_catalog.importer import digest, load, normalize, report_scan, suspicious_ci_change
from poets_catalog.models import Author, Material, RightsReview, Work, WorkVersion, WorkParagraph

FIXTURES = Path(__file__).parent / "fixtures"


def test_scan_and_text_normalization():
    """确认只读扫描统计、繁简转换与稳定 JSON 摘要。"""
    result = report_scan(FIXTURES / "v1", ["tang", "ci"])
    assert result["tang"]["work_records"] == 2
    assert result["ci"]["work_records"] == 1
    assert normalize("蘇軾 明月") == "苏轼 明月"
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_ci_identity_shift_detection():
    """同位置宋词的署名变化不能自动视为旧作品的新版本。"""
    old = {"author": "丙", "rhythmic": "清平乐", "paragraphs": ["月照江山。"]}
    assert suspicious_ci_change(old, {**old, "author": "丁"})
    assert not suspicious_ci_change(old, {**old, "paragraphs": ["月照江山。", "续句。"]})


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_repeatable_import_and_versioning(monkeypatch):
    """同一源快照重跑不重复；修订正文产生新版本并保持段落顺序。"""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    v1 = FIXTURES / "v1"
    v2 = FIXTURES / "v2"
    with engine().connect() as conn:
        baseline = conn.scalar(select(func.count()).select_from(WorkVersion))
    first = load(v1, ["tang", "ci"], "test-v1")
    assert not first["issues"]
    assert load(v1, ["tang", "ci"], "test-v1")["totals"]["tang_changed"] == 0
    changed = load(v2, ["tang", "ci"], "test-v2")
    assert changed["totals"]["tang_changed"] == 1
    assert any("possible identity shift" in issue for issue in changed["issues"])
    with engine().connect() as conn:
        works = dict(conn.execute(select(Work.source_lookup_key, Work.id).where(
            Work.source_lookup_key.in_(["tang_poem:work-1", "tang_poem:work-2", "song_ci:宋词/ci.song.0.json:0"]))).all())
        assert len(works) == 3
        ambiguous = conn.scalar(select(Work.author_id).where(Work.source_lookup_key == "tang_poem:work-2"))
        assert ambiguous is None
        target = works["tang_poem:work-1"]
        assert conn.scalar(select(func.count()).select_from(WorkVersion)) == (
            baseline + first["totals"]["tang_changed"] + first["totals"]["ci_changed"]
            + changed["totals"]["tang_changed"] + changed["totals"]["ci_changed"]
        )
        current = conn.scalar(select(WorkVersion.id).where(WorkVersion.work_id == target, WorkVersion.is_current.is_(True)))
        assert conn.scalar(select(func.count()).select_from(WorkParagraph).where(WorkParagraph.work_version_id == current)) == 3
        assert conn.scalar(select(func.count()).select_from(Material).where(Material.workflow_status == "published")) == 0
        assert conn.scalar(select(func.count()).select_from(RightsReview)) == 0


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_public_view_requires_matching_approval(monkeypatch):
    """即便有审核记录，内容摘要不一致也不可通过公开视图。"""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    load(FIXTURES / "v1", ["tang"], "test-v1")
    db = engine()
    try:
        with db.connect() as conn:
            transaction = conn.begin()
            try:
                version = conn.execute(select(WorkVersion.material_id, WorkVersion.content_hash).limit(1)).one()
                assert conn.scalar(text("SELECT count(*) FROM public_work_versions")) == 0
                conn.execute(insert(RightsReview).values(material_id=version.material_id, decision="approved",
                    legal_basis="test only", permitted_scope="web", evidence_uri="test://permission", reviewer="test", reviewed_hash="wrong"))
                conn.execute(update(Material).where(Material.id == version.material_id).values(workflow_status="published"))
                assert conn.scalar(text("SELECT count(*) FROM public_work_versions")) == 0
                conn.execute(update(RightsReview).where(RightsReview.material_id == version.material_id)
                    .values(reviewed_hash=version.content_hash))
                assert conn.scalar(text("SELECT count(*) FROM public_work_versions")) == 1
            finally:
                transaction.rollback()
    finally:
        db.dispose()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_ambiguous_author_is_attribution_not_person(monkeypatch):
    """同名且无源 ID 的作者记录只留出处，不凭名字生成规范人物。"""
    from poets_catalog.models import AuthorAttribution
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    result = load(FIXTURES / "ambiguous", ["ci"], "test-ambiguous")
    assert not result["issues"]
    with engine().connect() as conn:
        attrs = conn.execute(select(AuthorAttribution.author_id, AuthorAttribution.match_status)
            .where(AuthorAttribution.source_path == "宋词/author.song.json",
                   AuthorAttribution.original_name == "蔡")).all()
        assert any(aid is None and status == "ambiguous" for aid,status in attrs)
        work = conn.scalar(select(Work.author_id).where(Work.source_lookup_key == "song_ci:宋词/ci.song.3000.json:0"))
        assert work is None
        assert conn.scalar(select(func.count()).select_from(AuthorAttribution)
            .where(AuthorAttribution.source_path == "宋词/author.song.json",
                   AuthorAttribution.original_name == "蔡",
                   AuthorAttribution.author_id.is_not(None))) == 0


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_author_uuid_survives_traditional_simplified_change(monkeypatch):
    """同一来源位置的繁简异体变化不改变作者的公开 UUID。"""
    from uuid import UUID
    from poets_catalog.importer import ensure_source, insert_authors
    from poets_catalog.models import AuthorAttribution
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    db = engine()
    try:
        identifiers = []
        for directory, revision in (("variant_a", "variant-a-v2"), ("variant_b", "variant-b-v2")):
            root = FIXTURES / directory
            with db.begin() as conn:
                source_id = ensure_source(conn, revision)
                insert_authors(conn, root, root / "宋词/author.variant.json", "ci", "song", source_id)
                author_id = conn.scalar(select(AuthorAttribution.author_id).where(
                    AuthorAttribution.source_id == source_id,
                    AuthorAttribution.source_path == "宋词/author.variant.json",
                    AuthorAttribution.source_index == 1))
                identifiers.append(author_id)
        assert identifiers[0] is not None and identifiers[0] == identifiers[1]
        with db.connect() as conn:
            public_id = conn.scalar(select(Author.public_id).where(Author.id == identifiers[0]))
            assert isinstance(public_id, UUID)
            assert str(public_id) not in ("苏轼", "蘇軾")
    finally:
        db.dispose()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_editorial_cross_version_and_rights_guards(monkeypatch):
    """数据库阻止跨版本译文、错误拼音和无证据批准等关联。"""
    from uuid import uuid4
    from sqlalchemy.exc import DBAPIError
    from poets_catalog.models import (Source, WorkDate, TranslationEdition, TranslationBlock,
                                      Commentary, PinyinSet)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    load(FIXTURES / "v1", ["tang"], "test-v1")
    db = engine()
    try:
        with db.connect() as conn:
            outer = conn.begin()
            try:
                rows = conn.execute(select(WorkVersion.id, WorkVersion.work_id, WorkVersion.content_hash,
                    WorkParagraph.id).join(WorkParagraph, WorkParagraph.work_version_id == WorkVersion.id)
                    .where(WorkVersion.is_current.is_(True)).order_by(WorkVersion.id).limit(10)).all()
                first = rows[0]
                other = next(r for r in rows if r.work_id != first.work_id)
                material_id = conn.scalar(insert(Material).values(external_key=f"test:{uuid4()}",
                    kind="translation", language_tag="en", content_hash="test-hash", workflow_status="staged").returning(Material.id))
                edition_id = conn.scalar(insert(TranslationEdition).values(work_version_id=first.id,
                    material_id=material_id, language_tag="en", alignment_mode="paragraph").returning(TranslationEdition.id))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(insert(TranslationBlock).values(edition_id=edition_id,
                            work_paragraph_id=other[3], block_index=0, body="wrong version"))
                conn.execute(insert(TranslationBlock).values(edition_id=edition_id,
                    work_paragraph_id=first[3], block_index=0, body="matched"))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(update(TranslationEdition).where(TranslationEdition.id == edition_id)
                            .values(work_version_id=other.id))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(update(WorkParagraph).where(WorkParagraph.id == first[3])
                            .values(body="silently changed"))
                commentary_material = conn.scalar(insert(Material).values(external_key=f"test:{uuid4()}",
                    kind="commentary", content_hash="test-comment", workflow_status="staged").returning(Material.id))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(insert(Commentary).values(work_version_id=first.id,
                            work_paragraph_id=other[3], material_id=commentary_material, body="wrong"))
                pinyin_material = conn.scalar(insert(Material).values(external_key=f"test:{uuid4()}",
                    kind="pinyin", content_hash="test-pinyin", workflow_status="staged").returning(Material.id))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(insert(PinyinSet).values(work_version_id=first.id,
                            material_id=pinyin_material, alignment_sha256="wrong", tokens=[]))
                source_id = conn.scalar(select(Source.id).limit(1))
                conn.execute(insert(WorkDate).values(work_id=first.work_id, source_id=source_id,
                    year_start=1000, year_end=1001, date_precision="range",
                    confidence="high", is_preferred=True))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(insert(WorkDate).values(work_id=first.work_id, source_id=source_id,
                            year_start=1002, year_end=1003, date_precision="range",
                            confidence="high", is_preferred=True))
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(insert(RightsReview).values(material_id=material_id,
                            decision="approved", reviewer="test", reviewed_hash="test-hash"))
            finally:
                outer.rollback()
    finally:
        db.dispose()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_work_public_id_is_opaque_and_stable(monkeypatch):
    """作品公开 UUID 在重复导入后不随来源查找键变化。"""
    from uuid import UUID
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    load(FIXTURES / "v1", ["tang"], "test-v1")
    with engine().connect() as conn:
        before = conn.scalar(select(Work.public_id).where(Work.source_lookup_key == "tang_poem:work-1"))
    load(FIXTURES / "v1", ["tang"], "test-v1")
    with engine().connect() as conn:
        after = conn.scalar(select(Work.public_id).where(Work.source_lookup_key == "tang_poem:work-1"))
    assert isinstance(before, UUID) and before == after
