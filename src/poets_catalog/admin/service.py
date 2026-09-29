"""后台写入服务：事务中维护版本、检索索引、审核历史与审计日志。"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..importer import digest, normalize
from ..models import (
    AdminAuditLog, Author, AuthorBiography, Commentary, Material, RightsReview,
    Source, TranslationBlock, TranslationEdition, Work, WorkParagraph, WorkSearch, WorkVersion,
    WorkDate, AuthorEvent, AuthorAttribution, PinyinSet,
)
from .schemas import (AuthorInput, BiographyInput, CommentaryInput, ReviewInput, SourceInput,
                      TranslationInput, WorkInput, WorkDateInput, AuthorEventInput, PinyinInput)


def audit(session: Session, action: str, entity_type: str, entity_id: int, summary: dict | None = None):
    """不存令牌、授权证据全文和诗词正文，只记录可追溯的操作摘要。"""
    session.add(AdminAuditLog(actor=session.info.get("admin_actor", "unknown"), action=action, entity_type=entity_type,
                              entity_id=entity_id, summary=summary or {}))


def require(session: Session, model, ident: int):
    value = session.get(model, ident)
    if value is None:
        raise HTTPException(404, "记录不存在")
    return value


def editorial_source(session: Session) -> int:
    source = session.scalar(select(Source).where(Source.key == "admin:editorial"))
    if source is None:
        source = Source(key="admin:editorial", kind="editorial", title="诗卷人工编辑",
                        license_note="编辑内容需要逐条审核；来源并不等于授权")
        session.add(source)
        session.flush()
    return source.id


def choose_source(session: Session, source_id: int | None) -> int:
    if source_id is None:
        return editorial_source(session)
    require(session, Source, source_id)
    return source_id


def create_source(session: Session, data: SourceInput):
    if session.scalar(select(Source.id).where(Source.key == data.key)) is not None:
        raise HTTPException(409, "来源键已存在")
    source = Source(**data.model_dump())
    session.add(source)
    session.flush()
    audit(session, "create", "source", source.id, {"key": source.key})
    return source


def update_source(session: Session, ident: int, data: SourceInput):
    source = require(session, Source, ident)
    if source.kind == "repository_snapshot":
        raise HTTPException(409, "导入来源快照不可通过后台改写；请记录新来源")
    duplicate = session.scalar(select(Source.id).where(Source.key == data.key, Source.id != ident))
    if duplicate:
        raise HTTPException(409, "来源键已存在")
    for key, value in data.model_dump().items():
        setattr(source, key, value)
    audit(session, "update", "source", ident, {"key": source.key})
    return source


def create_author(session: Session, data: AuthorInput):
    author = Author(**data.model_dump())
    session.add(author)
    session.flush()
    audit(session, "create", "author", author.id, {"name": author.canonical_name})
    return author


def update_author(session: Session, ident: int, data: AuthorInput):
    author = require(session, Author, ident)
    for key, value in data.model_dump().items():
        setattr(author, key, value)
    audit(session, "update", "author", ident, {"name": author.canonical_name})
    return author


def archive_author(session: Session, ident: int):
    author = session.get(Author, ident, with_for_update=True)
    if author is None:
        raise HTTPException(404, "作者不存在")
    if author.identity_status == "archived":
        raise HTTPException(409, "作者已归档，请使用恢复操作")
    author.identity_status = "archived"
    audit(session, "archive", "author", ident)


def restore_author(session: Session, ident: int):
    """只恢复到待考证，不凭恢复动作宣称身份已人工核验。"""
    author = session.get(Author, ident, with_for_update=True)
    if author is None:
        raise HTTPException(404, "作者不存在")
    if author.identity_status != "archived":
        raise HTTPException(409, "作者未归档，无需恢复")
    author.identity_status = "unverified"
    audit(session, "restore", "author", ident)
    return author


def validate_author(session: Session, author_id: int | None, name: str):
    if author_id is not None:
        author = require(session, Author, author_id)
        if author.identity_status == "archived":
            raise HTTPException(422, "不可关联已归档作者")
        # 有异名、异体时允许由管理员明确关联，原始署名仍单独保留。


def write_version(session: Session, work: Work, data: WorkInput, source_id: int):
    """原文不可 UPDATE：先切换旧版 current，再插入新版本及有序段落和检索字段。"""
    last = session.scalar(select(func.max(WorkVersion.version_number)).where(WorkVersion.work_id == work.id)) or 0
    if last:
        session.execute(update(WorkVersion).where(WorkVersion.work_id == work.id, WorkVersion.is_current.is_(True))
                        .values(is_current=False))
    payload = {"author": data.author_name, "title": data.title, "rhythmic": data.rhythmic,
               "paragraphs": data.paragraphs, "tags": data.tags, "prologue": data.prologue}
    content_hash = digest(payload)
    material = Material(external_key=f"admin:work:{work.public_id}:{last + 1}", kind="original",
                        source_id=source_id, language_tag="zh", origin_type="editorial",
                        workflow_status="staged", content_hash=content_hash)
    session.add(material)
    session.flush()
    version = WorkVersion(work_id=work.id, material_id=material.id, source_id=source_id,
                          source_path=f"admin/{work.public_id}", source_index=last,
                          title=data.title, rhythmic=data.rhythmic, prologue=data.prologue,
                          tags=data.tags, raw_payload=payload, content_text="\n".join(data.paragraphs),
                          search_text=normalize(" ".join([data.title or "", data.rhythmic or "",
                                                          data.author_name, *data.paragraphs])),
                          content_hash=content_hash, version_number=last + 1, is_current=True)
    session.add(version)
    session.flush()
    session.add_all(WorkParagraph(work_version_id=version.id, paragraph_index=i, body=line)
                    for i, line in enumerate(data.paragraphs))
    session.add(WorkSearch(work_version_id=version.id, normalized_author=normalize(data.author_name),
                           normalized_title=normalize(data.title or data.rhythmic or ""),
                           normalized_tags=normalize(" ".join(data.tags))))
    return version


def create_work(session: Session, data: WorkInput):
    validate_author(session, data.author_id, data.author_name)
    source_id = editorial_source(session)
    work_id = uuid4()
    work = Work(public_id=work_id, source_lookup_key=f"admin:{work_id}", genre=data.genre,
                author_id=data.author_id, original_author_name=data.author_name)
    session.add(work)
    session.flush()
    version = write_version(session, work, data, source_id)
    audit(session, "create", "work", work.id, {"version": version.id, "title": data.title or data.rhythmic})
    return work


def revise_work(session: Session, ident: int, data: WorkInput):
    work = session.get(Work, ident, with_for_update=True)
    if work is None:
        raise HTTPException(404, "作品不存在")
    if work.identity_status == "archived":
        raise HTTPException(409, "已归档作品不可编辑，请先恢复")
    if data.genre != work.genre:
        raise HTTPException(422, "作品类型不可改写；请新建作品")
    # 规范人物关联属于身份考据，不在原文修订中修改；未提供 ID 时沿用旧关联。
    if data.author_id is not None and data.author_id != work.author_id:
        raise HTTPException(422, "规范人物关联不能在作品编辑中变更；请走独立的身份核对流程")
    source_id = editorial_source(session)
    work.original_author_name = data.author_name
    version = write_version(session, work, data, source_id)
    audit(session, "revise", "work", work.id, {"version": version.id, "title": data.title or data.rhythmic})
    return work


def archive_work(session: Session, ident: int, restore: bool = False):
    work = session.get(Work, ident, with_for_update=True)
    if work is None:
        raise HTTPException(404, "作品不存在")
    if restore and work.identity_status != "archived":
        raise HTTPException(409, "作品未归档，无需恢复")
    if not restore and work.identity_status == "archived":
        raise HTTPException(409, "作品已归档，请使用恢复操作")
    work.identity_status = "normal" if restore else "archived"
    versions = session.scalars(select(WorkVersion).where(WorkVersion.work_id == work.id)).all()
    for version in versions:
        material = require(session, Material, version.material_id)
        material.workflow_status = "staged" if restore else "withdrawn"
    audit(session, "restore" if restore else "archive", "work", ident)
    return work


def create_biography(session: Session, author_id: int, data: BiographyInput, revises_biography_id: int | None = None):
    require(session, Author, author_id)
    if revises_biography_id is not None:
        previous = require(session, AuthorBiography, revises_biography_id)
        if previous.author_id != author_id:
            raise HTTPException(422, "不能跨作者修订小传")
    source_id = choose_source(session, data.source_id)
    material = Material(external_key=f"admin:bio:{uuid4()}", kind="biography", source_id=source_id,
                        language_tag="zh", origin_type="editorial", workflow_status="staged",
                        content_hash=digest({"body": data.body, "summary": data.summary}))
    session.add(material)
    session.flush()
    bio = AuthorBiography(author_id=author_id, material_id=material.id,
                          revises_biography_id=revises_biography_id,
                          body=data.body, summary=data.summary)
    session.add(bio)
    session.flush()
    audit(session, "revise" if revises_biography_id is not None else "create", "biography", bio.id,
          {"author_id": author_id, "material_id": material.id,
           "revises_biography_id": revises_biography_id})
    return bio


def create_commentary(session: Session, work_id: int, data: CommentaryInput, revises_id: int | None = None):
    work = require(session, Work, work_id)
    if revises_id is not None:
        previous = require(session, Commentary, revises_id)
        previous_version = require(session, WorkVersion, previous.work_version_id)
        if previous_version.work_id != work_id:
            raise HTTPException(422, "不能跨作品修订内容")
    version = session.scalar(select(WorkVersion).where(WorkVersion.work_id == work.id, WorkVersion.is_current.is_(True)))
    if version is None:
        raise HTTPException(409, "作品缺少当前版本")
    source_id = choose_source(session, data.source_id)
    material = Material(external_key=f"admin:commentary:{uuid4()}", kind="commentary", source_id=source_id,
                        language_tag="zh", origin_type="editorial", workflow_status="staged",
                        content_hash=digest(data.model_dump()))
    session.add(material)
    session.flush()
    item = Commentary(work_version_id=version.id, material_id=material.id, revises_id=revises_id, title=data.title,
                      body=data.body, commentator_name=data.commentator_name)
    session.add(item)
    session.flush()
    audit(session, "revise" if revises_id is not None else "create", "commentary", item.id, {"work_id": work_id, "material_id": material.id})
    return item


def create_translation(session: Session, work_id: int, data: TranslationInput, revises_id: int | None = None):
    work = require(session, Work, work_id)
    if revises_id is not None:
        previous = require(session, TranslationEdition, revises_id)
        previous_version = require(session, WorkVersion, previous.work_version_id)
        if previous_version.work_id != work_id:
            raise HTTPException(422, "不能跨作品修订内容")
    version = session.scalar(select(WorkVersion).where(WorkVersion.work_id == work.id, WorkVersion.is_current.is_(True)))
    if version is None:
        raise HTTPException(409, "作品缺少当前版本")
    source_id = choose_source(session, data.source_id)
    material = Material(external_key=f"admin:translation:{uuid4()}", kind="translation", source_id=source_id,
                        language_tag=data.language_tag, origin_type="editorial", workflow_status="staged",
                        content_hash=digest(data.model_dump()))
    session.add(material)
    session.flush()
    edition = TranslationEdition(work_version_id=version.id, material_id=material.id, revises_id=revises_id,
                                 language_tag=data.language_tag, translator_name=data.translator_name,
                                 alignment_mode="whole")
    session.add(edition)
    session.flush()
    session.add_all(TranslationBlock(edition_id=edition.id, block_index=i, body=block)
                    for i, block in enumerate(data.blocks))
    audit(session, "revise" if revises_id is not None else "create", "translation", edition.id, {"work_id": work_id, "material_id": material.id})
    return edition


def withdraw_material(session: Session, ident: int):
    material = require(session, Material, ident)
    if material.kind == "original":
        raise HTTPException(409, "原文请通过作品归档撤下，不得单独删除当前版本")
    material.workflow_status = "withdrawn"
    audit(session, "withdraw", "material", ident, {"kind": material.kind})
    return material


def review_material(session: Session, ident: int, data: ReviewInput):
    """保留历史审核行；审核新内容必须针对当前摘要，撤销后公开视图立即失效。"""
    material = session.get(Material, ident, with_for_update=True)
    if material is None:
        raise HTTPException(404, "材料不存在")
    if data.decision == "approved":
        if material.workflow_status == "withdrawn":
            raise HTTPException(409, "已撤下材料不可批准")
        if data.expires_at and data.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "授权有效期须在将来")
    session.execute(update(RightsReview).where(RightsReview.material_id == ident, RightsReview.is_current.is_(True))
                    .values(is_current=False))
    review = RightsReview(material_id=ident, decision=data.decision,
                          legal_basis=data.legal_basis, permitted_scope=data.permitted_scope,
                          evidence_uri=data.evidence_uri, reviewer=session.info.get("admin_actor", "unknown"),
                          expires_at=data.expires_at, reason=data.reason,
                          reviewed_hash=material.content_hash, is_current=True)
    session.add(review)
    session.flush()
    if data.decision == "approved":
        material.workflow_status = "published"
    elif material.workflow_status != "withdrawn":
        material.workflow_status = "staged"
    audit(session, "review", "material", ident, {"decision": data.decision, "review_id": review.id})
    return review


def bind_attribution(session: Session, ident: int, author_id: int | None):
    """人工消歧只调整人物关联，保留原署名和原始传记不变。"""
    record = require(session, AuthorAttribution, ident)
    if author_id is not None:
        author = require(session, Author, author_id)
        if author.identity_status == "archived":
            raise HTTPException(422, "不可关联已归档人物")
    record.author_id = author_id
    record.match_status = "reviewed" if author_id is not None else "ambiguous"
    audit(session, "match", "attribution", ident, {"author_id": author_id})
    return record


def save_work_date(session: Session, work_id: int, data: WorkDateInput, ident: int | None = None):
    require(session, Work, work_id)
    source_id = choose_source(session, data.source_id)
    item = require(session, WorkDate, ident) if ident else WorkDate(work_id=work_id)
    if item.work_id != work_id:
        raise HTTPException(422, "时间记录不属于该作品")
    if data.is_preferred:
        session.query(WorkDate).filter(WorkDate.work_id == work_id, WorkDate.is_preferred.is_(True)).update(
            {WorkDate.is_preferred: False}, synchronize_session=False)
    for key, value in data.model_dump(exclude={"source_id"}).items():
        setattr(item, key, value)
    item.source_id = source_id
    session.add(item)
    session.flush()
    audit(session, "update" if ident else "create", "work_date", item.id, {"work_id": work_id})
    return item


def save_author_event(session: Session, author_id: int, data: AuthorEventInput, ident: int | None = None):
    require(session, Author, author_id)
    source_id = choose_source(session, data.source_id)
    item = require(session, AuthorEvent, ident) if ident else AuthorEvent(author_id=author_id)
    if item.author_id != author_id:
        raise HTTPException(422, "事件不属于该作者")
    for key, value in data.model_dump(exclude={"source_id"}).items():
        setattr(item, key, value)
    item.source_id = source_id
    session.add(item)
    session.flush()
    audit(session, "update" if ident else "create", "author_event", item.id, {"author_id": author_id})
    return item


def create_pinyin(session: Session, work_id: int, data: PinyinInput, revises_id: int | None = None):
    """逐字标注必须对齐当前不可变版本的段落和 Unicode 字符。"""
    require(session, Work, work_id)
    if revises_id is not None:
        previous = require(session, PinyinSet, revises_id)
        previous_version = require(session, WorkVersion, previous.work_version_id)
        if previous_version.work_id != work_id:
            raise HTTPException(422, "不能跨作品修订内容")
    version = session.scalar(select(WorkVersion).where(WorkVersion.work_id == work_id, WorkVersion.is_current.is_(True)))
    if version is None:
        raise HTTPException(409, "作品缺少当前版本")
    paragraphs = session.scalars(select(WorkParagraph.body).where(WorkParagraph.work_version_id == version.id)
                                 .order_by(WorkParagraph.paragraph_index)).all()
    seen = set()
    for token in data.tokens:
        position = (token.paragraph_index, token.char_index)
        if position in seen or token.paragraph_index >= len(paragraphs) or \
           token.char_index >= len(paragraphs[token.paragraph_index]) or \
           paragraphs[token.paragraph_index][token.char_index] != token.character:
            raise HTTPException(422, f"拼音标注无法对齐原文位置 {position}")
        seen.add(position)
    source_id = choose_source(session, data.source_id)
    serialized = [token.model_dump() for token in data.tokens]
    material = Material(external_key=f"admin:pinyin:{uuid4()}", kind="pinyin", source_id=source_id,
                        language_tag="zh-Latn", origin_type="editorial", workflow_status="staged",
                        content_hash=digest(serialized))
    session.add(material)
    session.flush()
    item = PinyinSet(work_version_id=version.id, material_id=material.id, revises_id=revises_id, tokens=serialized,
                     romanization=data.romanization, alignment_sha256=version.content_hash)
    session.add(item)
    session.flush()
    audit(session, "revise" if revises_id is not None else "create", "pinyin", item.id, {"work_id": work_id, "material_id": material.id})
    return item


def retire_work_date(session: Session, work_id: int, ident: int):
    """撤下时间说法而不销毁考据历史。"""
    item = require(session, WorkDate, ident)
    if item.work_id != work_id:
        raise HTTPException(422, "时间记录不属于该作品")
    item.review_status = "rejected"
    item.is_preferred = False
    audit(session, "retire", "work_date", ident, {"work_id": work_id})


def retire_author_event(session: Session, author_id: int, ident: int):
    """撤下生平事件但保留来源及历史审计。"""
    item = require(session, AuthorEvent, ident)
    if item.author_id != author_id:
        raise HTTPException(422, "事件不属于该作者")
    item.review_status = "rejected"
    audit(session, "retire", "author_event", ident, {"author_id": author_id})
