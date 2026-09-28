"""把数据库操作日志转换为审核员可阅读的事件摘要；保留原始编号供追溯。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AdminAuditLog, Author, AuthorAttribution, AuthorBiography, AuthorEvent,
    Commentary, Material, PinyinSet, Source, TranslationEdition, Work,
    WorkDate, WorkVersion,
)

ACTION_LABELS = {
    "create": "新增", "update": "修改", "revise": "修订原文",
    "archive": "归档", "restore": "恢复", "withdraw": "撤下",
    "retire": "撤下", "match": "核对身份",
}
ENTITY_LABELS = {
    "source": "来源", "author": "作者", "work": "作品", "material": "内容材料",
    "attribution": "原始署名", "biography": "作者小传", "commentary": "作品赏析",
    "translation": "作品译文", "pinyin": "逐字拼音", "work_date": "创作年代",
    "author_event": "生平事件",
}
MATERIAL_LABELS = {
    "original": "作品原文", "biography": "作者小传", "commentary": "作品赏析",
    "translation": "作品译文", "pinyin": "逐字拼音",
}
DECISION_LABELS = {"approved": "批准公开", "rejected": "拒绝公开", "revoked": "撤销公开"}


def work_label(session: Session, work_id: int | None) -> str | None:
    if not work_id:
        return None
    row = session.execute(
        select(Work.original_author_name, WorkVersion.title, WorkVersion.rhythmic)
        .join(WorkVersion, WorkVersion.work_id == Work.id)
        .where(Work.id == work_id, WorkVersion.is_current.is_(True))
    ).first()
    if row:
        return f"《{row.title or row.rhythmic or '无题'}》 · {row.original_author_name}"
    return None


def material_label(session: Session, material_id: int) -> str | None:
    material = session.get(Material, material_id)
    if material is None:
        return None
    prefix = MATERIAL_LABELS.get(material.kind, "内容材料")
    version = session.scalar(select(WorkVersion).where(WorkVersion.material_id == material_id))
    if version:
        return f"{prefix} · {work_label(session, version.work_id) or f'作品 #{version.work_id}'}"
    biography = session.scalar(select(AuthorBiography).where(AuthorBiography.material_id == material_id))
    if biography:
        author = session.get(Author, biography.author_id)
        return f"{prefix} · {author.canonical_name if author else f'作者 #{biography.author_id}'}"
    for model in (Commentary, TranslationEdition, PinyinSet):
        item = session.scalar(select(model).where(model.material_id == material_id))
        if item:
            related = session.get(WorkVersion, item.work_version_id)
            return f"{prefix} · {work_label(session, related.work_id) if related else '作品已移除'}"
    return prefix


def target_label(session: Session, row: AdminAuditLog) -> str:
    kind, ident = row.entity_type, row.entity_id
    summary = row.summary or {}
    if kind == "material":
        return material_label(session, ident) or f"内容材料 #{ident}（原记录已移除）"
    if kind == "work":
        return work_label(session, ident) or f"作品 #{ident}（原记录已移除）"
    if kind in {"author", "author_event"}:
        author_id = ident if kind == "author" else summary.get("author_id")
        author = session.get(Author, author_id) if author_id else None
        return f"{ENTITY_LABELS[kind]} · {author.canonical_name}" if author else f"{ENTITY_LABELS[kind]} #{ident}"
    if kind == "source":
        source = session.get(Source, ident)
        return f"来源 · {source.title}" if source else f"来源 #{ident}"
    if kind == "attribution":
        attribution = session.get(AuthorAttribution, ident)
        return f"原始署名 · {attribution.original_name}" if attribution else f"原始署名 #{ident}"
    if kind == "work_date":
        related = work_label(session, summary.get("work_id"))
        return f"创作年代 · {related or '作品 #' + str(summary.get('work_id', ident))}"
    if kind in {"biography", "commentary", "translation", "pinyin"}:
        return material_label(session, summary.get("material_id")) or f"{ENTITY_LABELS[kind]} #{ident}"
    return f"{ENTITY_LABELS.get(kind, kind)} #{ident}"


def present_audit(session: Session, row: AdminAuditLog) -> dict:
    """旧审计行仍可通过现存实体回填标题；记录丢失时安全降级为类型和编号。"""
    summary = row.summary or {}
    decision = summary.get("decision") if row.action == "review" else None
    action_label = DECISION_LABELS.get(decision) if decision else ACTION_LABELS.get(row.action)
    return {
        "id": row.id, "created_at": row.created_at.isoformat(), "actor": row.actor,
        "action": row.action, "action_label": action_label or row.action,
        "entity_type": row.entity_type, "entity_id": row.entity_id,
        "target_label": target_label(session, row), "summary": summary,
    }
