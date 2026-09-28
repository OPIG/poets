"""诗词目录的关系表：来源、人物、版本正文、扩展内容与权利审核。

表结构由 Alembic 迁移维护；这里的模型用于导入脚本和后续服务端查询。
原始文字与现代简介先入私有暂存，不因源仓库采用 MIT 就自动公开。
"""
from datetime import datetime
from uuid import UUID as PythonUUID
from typing import Any
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 表共享的元数据基类，供 Alembic 比较迁移。"""
    pass


def pk() -> Mapped[int]:
    """内部 bigint 主键；人物、作品另外使用独立的公开 UUID。"""
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


class Source(Base):
    """来源快照或外部出处。Git 提交号固定本次导入所依据的原始文件。"""
    __tablename__ = "sources"
    id: Mapped[int] = pk()
    key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    license_note: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Author(Base):
    """规范人物实体；public_id 不包含姓名，历史身份未核验时保持 unverified。"""
    __tablename__ = "authors"
    id: Mapped[int] = pk()
    # 对外稳定 ID；不要将姓名或文件位置编码到公开链接。
    public_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), unique=True, server_default=text("gen_random_uuid()"))
    canonical_name: Mapped[str] = mapped_column(Text)
    dynasty: Mapped[str] = mapped_column(String(16))
    birth_year_min: Mapped[int | None] = mapped_column(Integer)
    birth_year_max: Mapped[int | None] = mapped_column(Integer)
    death_year_min: Mapped[int | None] = mapped_column(Integer)
    death_year_max: Mapped[int | None] = mapped_column(Integer)
    identity_status: Mapped[str] = mapped_column(String(20), server_default="unverified")


class AuthorAttribution(Base):
    """原作者资料的一行及其原始署名；同名无法消歧时 author_id 留空。"""
    __tablename__ = "author_attributions"
    id: Mapped[int] = pk()
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    source_path: Mapped[str] = mapped_column(Text)
    source_index: Mapped[int] = mapped_column(Integer)
    original_id: Mapped[str | None] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    # 原文资料仅供内部校核，不能因导入就直接展示。
    raw_biography: Mapped[str | None] = mapped_column(Text)
    raw_short_biography: Mapped[str | None] = mapped_column(Text)
    match_status: Mapped[str] = mapped_column(String(20), server_default="unverified")
    __table_args__ = (UniqueConstraint("source_id", "source_path", "source_index"),
                      Index("ix_author_attributions_name", "original_name"))


class Work(Base):
    """作品身份和原始署名；source_lookup_key 仅为内部重导入定位键。"""
    __tablename__ = "works"
    id: Mapped[int] = pk()
    # 对外稳定 ID；不要将姓名或文件位置编码到公开链接。
    public_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), unique=True, server_default=text("gen_random_uuid()"))
    # 唐宋诗使用带类型的源 ID；无源 ID 的宋词暂用文件路径＋数组序号。
    source_lookup_key: Mapped[str] = mapped_column(Text, unique=True)
    genre: Mapped[str] = mapped_column(String(20))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id"))
    original_author_name: Mapped[str] = mapped_column(Text)
    identity_status: Mapped[str] = mapped_column(String(24), server_default="normal")
    __table_args__ = (CheckConstraint("genre IN ('tang_poem','song_poem','song_ci')"),
                      Index("ix_works_author_genre", "author_id", "genre"))


class Material(Base):
    """所有可展示文本的权利审核对象；版本正文和扩展内容各有独立材料。"""
    __tablename__ = "materials"
    id: Mapped[int] = pk()
    external_key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    language_tag: Mapped[str | None] = mapped_column(String(40))
    origin_type: Mapped[str] = mapped_column(String(32), server_default="imported")
    creator_name: Mapped[str | None] = mapped_column(Text)
    generation_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 默认 staged；发布必须再有当前有效的 rights_reviews 批准。
    workflow_status: Mapped[str] = mapped_column(String(24), server_default="staged")
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkVersion(Base):
    """一首作品的一次原文快照；校订产生新行而不是改写旧版。"""
    __tablename__ = "work_versions"
    id: Mapped[int] = pk()
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), unique=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    source_path: Mapped[str] = mapped_column(Text)
    source_index: Mapped[int] = mapped_column(Integer)
    original_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    rhythmic: Mapped[str | None] = mapped_column(Text)
    prologue: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # 保留完整来源 JSON，便于校对和重建；paragraphs 另行拆表用于对照。
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_text: Mapped[str] = mapped_column(Text)
    # 仅用于关键词检索的繁简归一化副本；展示仍读原始 title/段落。
    search_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    version_number: Mapped[int] = mapped_column(Integer)
    # 每首作品最多一条当前版本，由部分唯一索引保证。
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("work_id", "version_number"),
                      Index("uq_current_work_version", "work_id", unique=True, postgresql_where=text("is_current")),
                      Index("ix_work_version_source_locator", "source_path", "source_index"),
                      Index("ix_work_version_search_trgm", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}))


class WorkParagraph(Base):
    """隶属于具体原文版本的有序段落，供拼音和逐段译文对齐。"""
    __tablename__ = "work_paragraphs"
    id: Mapped[int] = pk()
    work_version_id: Mapped[int] = mapped_column(ForeignKey("work_versions.id"))
    paragraph_index: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("work_version_id", "paragraph_index"),)


class WorkDate(Base):
    """有出处的创作年代说法；区间与可信度不能从作者生卒年推断。"""
    __tablename__ = "work_dates"
    id: Mapped[int] = pk()
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    year_start: Mapped[int] = mapped_column(Integer)
    year_end: Mapped[int] = mapped_column(Integer)
    date_precision: Mapped[str] = mapped_column(String(24))
    confidence: Mapped[str] = mapped_column(String(24))
    rationale: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(24), server_default="pending")
    # 同一作品至多一条首选年代说法，其他考证仍可保留。
    is_preferred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (CheckConstraint("year_start <= year_end"),
                      Index("ix_work_dates_timeline", "year_start", "year_end"),
                      Index("uq_preferred_work_date", "work_id", unique=True, postgresql_where=text("is_preferred")))


class AuthorEvent(Base):
    """有出处的作者生平事件，供人物时间线使用。"""
    __tablename__ = "author_events"
    id: Mapped[int] = pk()
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    year_start: Mapped[int] = mapped_column(Integer)
    year_end: Mapped[int] = mapped_column(Integer)
    date_precision: Mapped[str] = mapped_column(String(24))
    event_label: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(24), server_default="pending")
    __table_args__ = (CheckConstraint("year_start <= year_end"),)


class AuthorBiography(Base):
    """人物简介正文；修订追加新行，并保留来源版本及独立的权利审查。"""
    __tablename__ = "author_biographies"
    id: Mapped[int] = pk()
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), unique=True)
    revises_biography_id: Mapped[int | None] = mapped_column(
        ForeignKey("author_biographies.id", name="fk_author_biographies_revises"),
        index=True,
    )
    body: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)


class TranslationEdition(Base):
    """一个作品版本的一种语言/译者译本，可对应多个译文段落。"""
    __tablename__ = "translation_editions"
    id: Mapped[int] = pk()
    work_version_id: Mapped[int] = mapped_column(ForeignKey("work_versions.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), unique=True)
    language_tag: Mapped[str] = mapped_column(String(40))
    translator_name: Mapped[str | None] = mapped_column(Text)
    alignment_mode: Mapped[str] = mapped_column(String(16))


class TranslationBlock(Base):
    """译文分块；整篇译文不绑定段落，逐段译文绑定同版本原文段落。"""
    __tablename__ = "translation_blocks"
    id: Mapped[int] = pk()
    edition_id: Mapped[int] = mapped_column(ForeignKey("translation_editions.id"))
    # 可空表示整篇；非空须与上层作品版本一致，由数据库触发器校验。
    work_paragraph_id: Mapped[int | None] = mapped_column(ForeignKey("work_paragraphs.id"))
    block_index: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("edition_id", "block_index"),)


class Commentary(Base):
    """赏析或评注；可针对整篇或某个原文版本的段落。"""
    __tablename__ = "commentaries"
    id: Mapped[int] = pk()
    work_version_id: Mapped[int] = mapped_column(ForeignKey("work_versions.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), unique=True)
    # 可空表示整篇；非空须与上层作品版本一致，由数据库触发器校验。
    work_paragraph_id: Mapped[int | None] = mapped_column(ForeignKey("work_paragraphs.id"))
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    commentator_name: Mapped[str | None] = mapped_column(Text)


class PinyinSet(Base):
    """按原文摘要对齐的逐字拼音标注；字符位置保存在 JSONB 中。"""
    __tablename__ = "pinyin_sets"
    id: Mapped[int] = pk()
    work_version_id: Mapped[int] = mapped_column(ForeignKey("work_versions.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), unique=True)
    romanization: Mapped[str] = mapped_column(String(40), server_default="hanyu-pinyin")
    # 按段落序号和字符偏移保存标注；写入时仍需应用层校验字符边界。
    tokens: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    alignment_sha256: Mapped[str] = mapped_column(String(64))
    review_status: Mapped[str] = mapped_column(String(24), server_default="pending")


class RightsReview(Base):
    """某份材料的授权/公版核验决定；仅最新有效且与内容摘要一致的批准可公开。"""
    __tablename__ = "rights_reviews"
    id: Mapped[int] = pk()
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    decision: Mapped[str] = mapped_column(String(24))
    legal_basis: Mapped[str | None] = mapped_column(Text)
    permitted_scope: Mapped[str | None] = mapped_column(Text)
    evidence_uri: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 审核针对具体内容摘要；文本改变后旧决定自动失效。
    reviewed_hash: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("uq_current_rights_review", "material_id", unique=True, postgresql_where=text("is_current")),)


class WorkSearch(Base):
    """独立的归一化索引，避免修改庞大的不可变原文版本行。"""
    __tablename__ = "work_searches"
    work_version_id: Mapped[int] = mapped_column(ForeignKey("work_versions.id"), primary_key=True)
    normalized_author: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    normalized_tags: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        Index("ix_work_search_author_trgm", "normalized_author", postgresql_using="gin", postgresql_ops={"normalized_author": "gin_trgm_ops"}),
        Index("ix_work_search_title_trgm", "normalized_title", postgresql_using="gin", postgresql_ops={"normalized_title": "gin_trgm_ops"}),
        Index("ix_work_search_tags_trgm", "normalized_tags", postgresql_using="gin", postgresql_ops={"normalized_tags": "gin_trgm_ops"}),
    )


class AdminAuditLog(Base):
    """追加式后台操作记录；不保存令牌或完整权利证据正文。"""
    __tablename__ = "admin_audit_logs"
    id: Mapped[int] = pk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(48))
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    __table_args__ = (Index("ix_admin_audit_logs_created_at", "created_at"),)


class AdminUser(Base):
    """管理员身份；只保存 Argon2id 密码摘要，不保存明文。"""
    __tablename__ = "admin_users"
    id: Mapped[int] = pk()
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    failed_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminSession(Base):
    """限时会话；数据库仅存 Cookie 摘要；CSRF 值可随登录会话恢复。"""
    __tablename__ = "admin_sessions"
    id: Mapped[int] = pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
