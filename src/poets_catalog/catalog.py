"""诗词读取层：API 与服务端页面共用同一套检索和版权可见性规则。"""
from urllib.parse import quote, urlsplit
from uuid import UUID

from sqlalchemy import and_, column, func, or_, select, table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .importer import normalize
from .models import (
    Author, AuthorBiography, Material, Source, Work, WorkParagraph,
    WorkSearch, WorkVersion,
)

PUBLIC_VERSIONS = table("public_work_versions", column("version_id"))
PUBLIC_MATERIALS = table("public_materials", column("id"))
GENRES = {"tang_poem": "唐诗", "song_poem": "宋诗", "song_ci": "宋词"}
FIELDS = {"all", "author", "title", "tag"}


class SearchIndexMissing(Exception):
    """数据库已迁移，但旧作品尚未执行 `poets-import reindex`。"""


def escaped_pattern(query: str) -> str:
    """防止输入中的百分号、下划线改变模糊检索的含义。"""
    return "%" + normalize(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def github_source_links(url: str | None, revision: str | None, relative_path: str) -> tuple[str | None, str | None]:
    """仓库地址、固定提交和相对路径共同组成可追溯的来源文件链接。"""
    parsed = urlsplit(url or "")
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None, None
    parts = [piece for piece in parsed.path.split("/") if piece]
    if len(parts) < 2:
        return None, None
    repo = f"https://github.com/{quote(parts[0])}/{quote(parts[1])}"
    if not revision or not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
        return repo, None
    return repo, f"{repo}/blob/{quote(revision, safe='')}/{quote(relative_path, safe='/')}"


# 阅读站及检索 API 的默认分页条数；显式传入 size 仍可按 API 限制调整。
DEFAULT_PAGE_SIZE = 20


class CatalogRepository:
    """仅提供只读查询；public 模式从已审核视图过滤，preview 模式供本机校对。"""

    def __init__(self, db: Engine, mode: str):
        self.db = db
        self.mode = mode

    def _base(self):
        stmt = (select(Work, WorkVersion, WorkSearch)
                .join(WorkVersion, and_(WorkVersion.work_id == Work.id, WorkVersion.is_current.is_(True)))
                .join(WorkSearch, WorkSearch.work_version_id == WorkVersion.id)
                .where(Work.identity_status == "normal"))
        if self.mode == "public":
            stmt = stmt.join(PUBLIC_VERSIONS, PUBLIC_VERSIONS.c.version_id == WorkVersion.id)
        return stmt

    def metadata(self):
        """公开模式只统计已审核作品，避免首页泄露暂存数量。"""
        with Session(self.db) as session:
            if self.mode == "preview":
                counts = dict(session.execute(select(Work.genre, func.count())
                    .where(Work.identity_status == "normal").group_by(Work.genre)).all())
            else:
                visible = self._base().subquery()
                counts = dict(session.execute(select(visible.c.genre, func.count()).group_by(visible.c.genre)).all())
            indexed = session.scalar(select(func.count()).select_from(WorkSearch)) or 0
            versions = session.scalar(select(func.count()).select_from(WorkVersion)) or 0
        return {"mode": self.mode, "counts": {key: counts.get(key, 0) for key in GENRES},
                "total": sum(counts.values()), "indexed_versions": indexed, "versions": versions}

    def search(self, q: str = "", field: str = "all", genre: str = "all", page: int = 1, size: int = DEFAULT_PAGE_SIZE):
        """作者／标题／标签 OR 命中；类别 AND 筛选；原文不参与关键词检索。"""
        with Session(self.db) as session:
            if not session.scalar(select(func.count()).select_from(WorkSearch)):
                raise SearchIndexMissing()
            stmt = self._base()
            if genre != "all":
                stmt = stmt.where(Work.genre == genre)
            if q.strip():
                pattern = escaped_pattern(q.strip())
                conditions = {
                    "author": WorkSearch.normalized_author.ilike(pattern, escape="\\"),
                    "title": WorkSearch.normalized_title.ilike(pattern, escape="\\"),
                    "tag": WorkSearch.normalized_tags.ilike(pattern, escape="\\"),
                }
                stmt = stmt.where(or_(*conditions.values()) if field == "all" else conditions[field])
            if not q.strip() and self.mode == "preview":
                count_stmt = select(func.count()).select_from(Work).where(Work.identity_status == "normal")
                if genre != "all":
                    count_stmt = count_stmt.where(Work.genre == genre)
                total = session.scalar(count_stmt) or 0
            else:
                total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
            rows = session.execute(stmt.order_by(Work.id).limit(size).offset((page - 1) * size)).all()
        return {"total": total, "page": page, "size": size, "items": [
            {"id": str(w.public_id), "genre": w.genre, "author": w.original_author_name,
             "title": v.title or v.rhythmic or "无题", "rhythmic": v.rhythmic,
             "excerpt": " ".join(v.content_text.splitlines()[:2])[:90], "tags": v.tags[:5]}
            for w, v, _ in rows]}

    def detail(self, public_id: UUID):
        """只返回当前可见的原文；公开模式中的作者简介亦须独立权利审核。"""
        with Session(self.db) as session:
            row = session.execute(self._base().where(Work.public_id == public_id)).first()
            if row is None:
                return None
            work, version, _ = row
            paragraphs = session.scalars(select(WorkParagraph.body)
                                         .where(WorkParagraph.work_version_id == version.id)
                                         .order_by(WorkParagraph.paragraph_index)).all()
            biography = None
            author_public_id = None
            if work.author_id is not None:
                author = session.get(Author, work.author_id)
                if author is not None and author.identity_status != "archived":
                    author_public_id = author.public_id
                    # 新版已审核小传优先，不能让早期导入的待审资料遮住已发布版。
                    bio_stmt = (select(AuthorBiography.body)
                                .join(Material, Material.id == AuthorBiography.material_id)
                                .where(AuthorBiography.author_id == work.author_id))
                    approved = bio_stmt.join(PUBLIC_MATERIALS, PUBLIC_MATERIALS.c.id == Material.id)
                    biography = session.scalar(approved.order_by(AuthorBiography.id.desc()).limit(1))
                    if biography is None and self.mode == "preview":
                        # 本机预览允许查看尚未核权的材料，但不回退到已撤下的版本。
                        biography = session.scalar(bio_stmt.where(Material.workflow_status != "withdrawn")
                            .order_by(AuthorBiography.id.desc()).limit(1))
                    if biography and biography.strip() in {"--", "—", "-"}:
                        biography = None
            source = session.execute(select(Source.title, Source.url, Source.commit_sha)
                                     .where(Source.id == version.source_id)).one_or_none()
            source_url, file_url = github_source_links(source.url, source.commit_sha, version.source_path) if source else (None, None)
            return {"id": str(work.public_id), "genre": work.genre,
                    "author": work.original_author_name,
                    "author_id": str(author_public_id) if author_public_id else None,
                    "author_bio": biography, "title": version.title or version.rhythmic or "无题",
                    "rhythmic": version.rhythmic, "paragraphs": paragraphs, "tags": version.tags,
                    "prologue": version.prologue, "source": source.title if source else None,
                    "source_url": source_url, "source_file_url": file_url, "mode": self.mode}

    def public_ids(self, offset: int, limit: int):
        """按固定顺序分批枚举已公开作品；用于 sitemap，绝不扫描暂存作品。"""
        if self.mode != "public":
            return []
        with Session(self.db) as session:
            stmt = self._base().with_only_columns(Work.public_id).order_by(Work.id).offset(offset).limit(limit)
            return [str(item) for item in session.scalars(stmt)]
