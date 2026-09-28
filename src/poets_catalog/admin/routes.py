"""后台独立路由：显式 Bearer 鉴权、受控 CRUD、版本与权利审核。"""
import hmac
import os
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select, case
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased

from ..models import (
    AdminAuditLog, Author, AuthorBiography, Commentary, Material, RightsReview,
    Source, TranslationEdition, TranslationBlock, Work, WorkVersion, WorkDate, AuthorEvent, AuthorAttribution, PinyinSet,
)
from . import service, auth
from .audit_view import present_audit
from ..catalog import github_source_links
from .schemas import (AuthorInput, BiographyInput, CommentaryInput, ReviewInput, SourceInput,
                      TranslationInput, WorkInput, WorkDateInput, AuthorEventInput, PinyinInput)

ADMIN_TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                              autoescape=select_autoescape(["html"]))


def register_admin(app, db: Engine) -> None:
    """未配置足够长度的密钥时完全不注册后台路由。"""
    allow_remote = os.getenv("ADMIN_ALLOW_REMOTE") == "1"
    if allow_remote and not os.getenv("SITE_URL", "").startswith("https://"):
        raise RuntimeError("远程后台必须配置 HTTPS SITE_URL")
    router = APIRouter(prefix="/admin", include_in_schema=False)
    app.mount("/admin/static", StaticFiles(directory=Path(__file__).parent / "static"), name="admin-static")

    def same_origin(request: Request):
        # 登录与所有写操作均验证 Origin；无 Origin 的脚本请求仍须通过 CSRF。
        origin = request.headers.get("origin")
        if origin:
            expected = os.getenv("SITE_URL", "").rstrip("/") if allow_remote else str(request.base_url).rstrip("/")
            if not hmac.compare_digest(origin, expected):
                raise HTTPException(403, "跨站来源不可访问后台")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "跨站请求不可访问后台")

    def guard(request: Request):
        host = request.client.host if request.client else ""
        if not allow_remote and host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(403, "后台仅允许本机访问")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            same_origin(request)
        with Session(db, join_transaction_mode="create_savepoint") as session:
            found = auth.require_session(session, request,
                mutation=request.method not in ("GET", "HEAD", "OPTIONS"))
            request.state.admin_actor = found[0].username
        return True

    def session(request: Request):
        with Session(db, join_transaction_mode="create_savepoint") as current:
            current.info["admin_actor"] = getattr(request.state, "admin_actor", "unknown")
            yield current

    def transaction(request: Request):
        with Session(db, join_transaction_mode="create_savepoint") as current, current.begin():
            current.info["admin_actor"] = getattr(request.state, "admin_actor", "unknown")
            yield current

    class LoginInput(BaseModel):
        username: str = Field(min_length=1, max_length=80)
        password: str = Field(min_length=1, max_length=256)

    @router.get("", response_class=HTMLResponse)
    def dashboard(request: Request):
        # 登录页不含资料和凭据；实际数据只能从已鉴权的 API 读取。
        return HTMLResponse(ADMIN_TEMPLATES.get_template("index.html").render(), headers={
            "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        })

    @router.post("/api/login")
    def login(data: LoginInput, request: Request, response: Response):
        same_origin(request)
        with Session(db, join_transaction_mode="create_savepoint") as session, session.begin():
            user = auth.authenticate(session, data.username, data.password)
            if user is not None:
                token, csrf, expires = auth.new_session(session, user)
                username = user.username
        # 抛出异常前先提交事务，否则失败次数会被回滚。
        if user is None:
            raise HTTPException(401, "账号或密码错误，或账号暂时锁定")
        response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_SECONDS,
            httponly=True, secure=allow_remote, samesite="strict", path="/admin")
        response.headers["Cache-Control"] = "no-store"
        return {"username": username, "csrf_token": csrf, "expires_at": expires.isoformat()}

    @router.get("/api/session", dependencies=[Depends(guard)])
    def current_session(request: Request, current: Session = Depends(session)):
        _, row = auth.require_session(current, request)
        # HttpOnly Cookie 仍不可读；经鉴权的同源请求恢复写操作 CSRF 值。
        return {"username": request.state.admin_actor, "csrf_token": row.csrf_token,
                "expires_at": row.expires_at.isoformat()}

    @router.post("/api/logout", dependencies=[Depends(guard)])
    def logout(request: Request, response: Response, current: Session = Depends(transaction)):
        _, row = auth.require_session(current, request, mutation=True)
        row.revoked_at = datetime.now(timezone.utc)
        response.delete_cookie(auth.COOKIE_NAME, path="/admin", secure=allow_remote, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return {"status": "logged_out"}

    @router.get("/api/overview", dependencies=[Depends(guard)])
    def overview(current: Session = Depends(session)):
        return {"works": current.scalar(select(func.count()).select_from(Work)),
                "authors": current.scalar(select(func.count()).select_from(Author)),
                "pending": current.scalar(select(func.count()).select_from(Material).where(Material.workflow_status == "staged")),
                "published": current.scalar(select(func.count()).select_from(Material).where(Material.workflow_status == "published"))}

    @router.get("/api/sources", dependencies=[Depends(guard)])
    def sources(response: Response, q: str = Query("", max_length=80), limit: int = Query(30, ge=1, le=100),
                offset: int = Query(0, ge=0), current: Session = Depends(session)):
        stmt = select(Source)
        if q.strip():
            pattern = f"%{q.strip()}%"
            stmt = stmt.where(Source.title.ilike(pattern) | Source.key.ilike(pattern))
        response.headers["X-Total-Count"] = str(current.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = current.scalars(stmt.order_by(Source.id.desc()).offset(offset).limit(limit)).all()
        return [{"id": row.id, "key": row.key, "kind": row.kind, "title": row.title, "url": row.url,
                 "license_note": row.license_note} for row in rows]

    @router.post("/api/sources", dependencies=[Depends(guard)])
    def source_create(data: SourceInput, current: Session = Depends(transaction)):
        row = service.create_source(current, data)
        return {"id": row.id, "key": row.key}

    @router.put("/api/sources/{ident}", dependencies=[Depends(guard)])
    def source_update(ident: int, data: SourceInput, current: Session = Depends(transaction)):
        row = service.update_source(current, ident, data)
        return {"id": row.id, "key": row.key}

    @router.get("/api/authors", dependencies=[Depends(guard)])
    def authors(response: Response, q: str = Query("", max_length=80),
                status: str = Query("all", pattern="^(all|normal|archived)$"),
                limit: int = Query(30, ge=1, le=100),
                offset: int = Query(0, ge=0), current: Session = Depends(session)):
        stmt = select(Author)
        if q.strip():
            stmt = stmt.where(Author.canonical_name.ilike(f"%{q.strip()}%"))
        if status == "archived":
            stmt = stmt.where(Author.identity_status == "archived")
        elif status == "normal":
            stmt = stmt.where(Author.identity_status != "archived")
        response.headers["X-Total-Count"] = str(current.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = current.scalars(stmt.order_by(Author.id.desc()).offset(offset).limit(limit)).all()
        return [{"id": row.id, "public_id": str(row.public_id), "name": row.canonical_name,
                 "dynasty": row.dynasty, "identity_status": row.identity_status} for row in rows]

    @router.get("/api/authors/{ident}", dependencies=[Depends(guard)])
    def author_detail(ident: int, current: Session = Depends(session)):
        author = service.require(current, Author, ident)
        # 返回所有小传版本及其独立审核状态；导入来源的具体文件只在管理端显示。
        rows = current.execute(
            select(AuthorBiography, Material, Source)
            .join(Material, AuthorBiography.material_id == Material.id)
            .outerjoin(Source, Source.id == Material.source_id)
            .where(AuthorBiography.author_id == ident)
            .order_by(AuthorBiography.id.desc())
        ).all()
        biographies = []
        for biography, material, source in rows:
            original = current.scalar(select(AuthorAttribution).where(
                AuthorAttribution.author_id == ident,
                AuthorAttribution.source_id == material.source_id,
                AuthorAttribution.raw_biography == biography.body,
            ).order_by(AuthorAttribution.id).limit(1)) if material.origin_type == "imported" else None
            _, file_url = github_source_links(source.url, source.commit_sha, original.source_path) \
                if source and original else (None, None)
            biographies.append({
                "id": biography.id, "material_id": material.id, "body": biography.body,
                "summary": biography.summary, "status": material.workflow_status,
                "origin_type": material.origin_type, "source_id": material.source_id,
                "source_title": source.title if source else None,
                "source_path": original.source_path if original else None,
                "source_index": original.source_index if original else None,
                "source_file_url": file_url,
                "revises_biography_id": biography.revises_biography_id,
            })
        return {"id": author.id, "public_id": str(author.public_id), "canonical_name": author.canonical_name,
                "dynasty": author.dynasty, "identity_status": author.identity_status,
                "birth_year_min": author.birth_year_min, "birth_year_max": author.birth_year_max,
                "death_year_min": author.death_year_min, "death_year_max": author.death_year_max,
                "biographies": biographies}

    @router.post("/api/authors", dependencies=[Depends(guard)])
    def author_create(data: AuthorInput, current: Session = Depends(transaction)):
        author = service.create_author(current, data)
        return {"id": author.id, "public_id": str(author.public_id)}

    @router.put("/api/authors/{ident}", dependencies=[Depends(guard)])
    def author_update(ident: int, data: AuthorInput, current: Session = Depends(transaction)):
        author = service.update_author(current, ident, data)
        return {"id": author.id, "public_id": str(author.public_id)}

    @router.delete("/api/authors/{ident}", dependencies=[Depends(guard)])
    def author_archive(ident: int, current: Session = Depends(transaction)):
        service.archive_author(current, ident)
        return {"id": ident, "status": "archived"}

    @router.post("/api/authors/{ident}/restore", dependencies=[Depends(guard)])
    def author_restore(ident: int, current: Session = Depends(transaction)):
        author = service.restore_author(current, ident)
        return {"id": author.id, "status": author.identity_status}

    @router.post("/api/authors/{ident}/biographies", dependencies=[Depends(guard)])
    def biography_create(ident: int, data: BiographyInput, current: Session = Depends(transaction)):
        bio = service.create_biography(current, ident, data)
        return {"id": bio.id, "material_id": bio.material_id}

    @router.post("/api/authors/{ident}/biographies/{biography_id}/revisions", dependencies=[Depends(guard)])
    def biography_revise(ident: int, biography_id: int, data: BiographyInput,
                         current: Session = Depends(transaction)):
        biography = service.create_biography(current, ident, data, revises_biography_id=biography_id)
        return {"id": biography.id, "material_id": biography.material_id,
                "revises_biography_id": biography.revises_biography_id}

    @router.get("/api/works", dependencies=[Depends(guard)])
    def works(response: Response, q: str = Query("", max_length=80),
              status: str = Query("all", pattern="^(all|normal|archived)$"),
              limit: int = Query(30, ge=1, le=100),
              offset: int = Query(0, ge=0), current: Session = Depends(session)):
        stmt = select(Work, WorkVersion).join(WorkVersion, WorkVersion.work_id == Work.id).where(WorkVersion.is_current.is_(True))
        if q.strip():
            pattern = f"%{q.strip()}%"
            stmt = stmt.where(Work.original_author_name.ilike(pattern) | WorkVersion.title.ilike(pattern) |
                              WorkVersion.rhythmic.ilike(pattern))
        if status != "all":
            stmt = stmt.where(Work.identity_status == status)
        response.headers["X-Total-Count"] = str(current.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = current.execute(stmt.order_by(Work.id.desc()).offset(offset).limit(limit)).all()
        return [{"id": w.id, "public_id": str(w.public_id), "genre": w.genre,
                 "author": w.original_author_name, "title": v.title or v.rhythmic or "无题",
                 "version": v.version_number, "status": w.identity_status, "material_id": v.material_id}
                for w, v in rows]

    @router.get("/api/works/{ident}", dependencies=[Depends(guard)])
    def work_detail(ident: int, current: Session = Depends(session)):
        work = service.require(current, Work, ident)
        versions = current.scalars(select(WorkVersion).where(WorkVersion.work_id == ident)
                                   .order_by(WorkVersion.version_number.desc())).all()
        return {"id": work.id, "public_id": str(work.public_id), "genre": work.genre,
                "author_id": work.author_id, "author_name": work.original_author_name,
                "identity_status": work.identity_status,
                "versions": [{"id": v.id, "number": v.version_number, "is_current": v.is_current,
                              "title": v.title, "rhythmic": v.rhythmic, "paragraphs": v.raw_payload.get("paragraphs", []),
                              "tags": v.tags, "prologue": v.prologue, "material_id": v.material_id} for v in versions]}

    @router.post("/api/works", dependencies=[Depends(guard)])
    def work_create(data: WorkInput, current: Session = Depends(transaction)):
        work = service.create_work(current, data)
        return {"id": work.id, "public_id": str(work.public_id)}

    @router.put("/api/works/{ident}", dependencies=[Depends(guard)])
    def work_revise(ident: int, data: WorkInput, current: Session = Depends(transaction)):
        work = service.revise_work(current, ident, data)
        return {"id": work.id, "public_id": str(work.public_id)}

    @router.delete("/api/works/{ident}", dependencies=[Depends(guard)])
    def work_archive(ident: int, current: Session = Depends(transaction)):
        service.archive_work(current, ident)
        return {"id": ident, "status": "archived"}

    @router.post("/api/works/{ident}/restore", dependencies=[Depends(guard)])
    def work_restore(ident: int, current: Session = Depends(transaction)):
        service.archive_work(current, ident, restore=True)
        return {"id": ident, "status": "normal", "review_required": True}

    @router.post("/api/works/{ident}/commentaries", dependencies=[Depends(guard)])
    def commentary_create(ident: int, data: CommentaryInput, current: Session = Depends(transaction)):
        item = service.create_commentary(current, ident, data)
        return {"id": item.id, "material_id": item.material_id}

    @router.post("/api/works/{ident}/translations", dependencies=[Depends(guard)])
    def translation_create(ident: int, data: TranslationInput, current: Session = Depends(transaction)):
        item = service.create_translation(current, ident, data)
        return {"id": item.id, "material_id": item.material_id}

    @router.get("/api/materials", dependencies=[Depends(guard)])
    def materials(response: Response, status: str = "staged", limit: int = Query(30, ge=1, le=100),
                  offset: int = Query(0, ge=0), current: Session = Depends(session)):
        if status not in {"staged", "published", "withdrawn", "all"}:
            raise HTTPException(422, "状态无效")
        # 每种材料关联到不同内容表；使用左连接一次补齐审核所需的标题、作者与出处。
        original = aliased(WorkVersion)
        biography = aliased(AuthorBiography)
        commentary = aliased(Commentary)
        translation = aliased(TranslationEdition)
        pinyin = aliased(PinyinSet)
        related_version = aliased(WorkVersion)
        work = aliased(Work)
        author = aliased(Author)
        source = aliased(Source)
        title = case(
            (original.id.is_not(None), func.coalesce(original.title, original.rhythmic, "无题")),
            (biography.id.is_not(None), "作者小传"),
            (commentary.id.is_not(None), func.coalesce(commentary.title, "作品赏析")),
            (translation.id.is_not(None), "作品译文"),
            (pinyin.id.is_not(None), "逐字拼音"),
            else_="待整理材料",
        )
        stmt = (select(Material.id, Material.kind, Material.workflow_status, Material.content_hash,
                       Material.language_tag, Material.source_id, source.title.label("source_title"),
                       title.label("display_title"),
                       func.coalesce(work.original_author_name, author.canonical_name).label("creator"),
                       work.id.label("related_work_id"))
                .outerjoin(original, original.material_id == Material.id)
                .outerjoin(biography, biography.material_id == Material.id)
                .outerjoin(commentary, commentary.material_id == Material.id)
                .outerjoin(translation, translation.material_id == Material.id)
                .outerjoin(pinyin, pinyin.material_id == Material.id)
                .outerjoin(related_version, related_version.id == func.coalesce(
                    original.id, commentary.work_version_id, translation.work_version_id, pinyin.work_version_id))
                .outerjoin(work, work.id == related_version.work_id)
                .outerjoin(author, author.id == biography.author_id)
                .outerjoin(source, source.id == Material.source_id))
        if status != "all":
            stmt = stmt.where(Material.workflow_status == status)
        # 计数不做所有关系表连接，保持大库分页性能。
        count_stmt = select(func.count()).select_from(Material)
        if status != "all":
            count_stmt = count_stmt.where(Material.workflow_status == status)
        response.headers["X-Total-Count"] = str(current.scalar(count_stmt) or 0)
        rows = current.execute(stmt.order_by(Material.id.desc()).offset(offset).limit(limit)).all()
        return [{"id": r.id, "kind": r.kind, "source_id": r.source_id,
                 "status": r.workflow_status, "content_hash": r.content_hash,
                 "language_tag": r.language_tag, "display_title": r.display_title,
                 "creator": r.creator, "source_title": r.source_title,
                 "related_work_id": r.related_work_id} for r in rows]

    @router.get("/api/materials/{ident}", dependencies=[Depends(guard)])
    def material_detail(ident: int, current: Session = Depends(session)):
        m = service.require(current, Material, ident)
        reviews = current.scalars(select(RightsReview).where(RightsReview.material_id == ident)
                                  .order_by(RightsReview.id.desc())).all()
        work_version = current.scalar(select(WorkVersion).where(WorkVersion.material_id == ident))
        biography = current.scalar(select(AuthorBiography).where(AuthorBiography.material_id == ident))
        commentary = current.scalar(select(Commentary).where(Commentary.material_id == ident))
        translation = current.scalar(select(TranslationEdition).where(TranslationEdition.material_id == ident))
        pinyin = current.scalar(select(PinyinSet).where(PinyinSet.material_id == ident))
        translated_body = "\n".join(current.scalars(select(TranslationBlock.body)
            .where(TranslationBlock.edition_id == translation.id).order_by(TranslationBlock.block_index)).all()) if translation else None
        source = current.get(Source, m.source_id) if m.source_id else None
        related_version = work_version or (current.get(WorkVersion, commentary.work_version_id) if commentary else None) or \
            (current.get(WorkVersion, translation.work_version_id) if translation else None) or \
            (current.get(WorkVersion, pinyin.work_version_id) if pinyin else None)
        related_work = current.get(Work, related_version.work_id) if related_version else None
        related_author = current.get(Author, biography.author_id) if biography else None
        display_title = (related_version.title or related_version.rhythmic or "无题") if related_version else \
            "作者小传" if biography else "待整理材料"
        if commentary and commentary.title:
            display_title = commentary.title
        return {"id": m.id, "kind": m.kind, "status": m.workflow_status,
                "display_title": display_title,
                "creator": related_work.original_author_name if related_work else \
                    related_author.canonical_name if related_author else None,
                "source_title": source.title if source else None,
                "source_id": m.source_id, "content_hash": m.content_hash,
                "body": (work_version.content_text if work_version else biography.body if biography else
                         commentary.body if commentary else translated_body if translation else
                         str(pinyin.tokens) if pinyin else None),
                "related_work_id": work_version.work_id if work_version else None,
                "related_author_id": biography.author_id if biography else None,
                "related_translation_id": translation.id if translation else None,
                "reviews": [{"id": r.id, "decision": r.decision, "is_current": r.is_current,
                             "legal_basis": r.legal_basis, "permitted_scope": r.permitted_scope,
                             "evidence_uri": r.evidence_uri, "reviewed_at": r.reviewed_at.isoformat()}
                            for r in reviews]}

    @router.delete("/api/materials/{ident}", dependencies=[Depends(guard)])
    def material_withdraw(ident: int, current: Session = Depends(transaction)):
        material = service.withdraw_material(current, ident)
        return {"id": material.id, "status": material.workflow_status}

    @router.post("/api/materials/{ident}/reviews", dependencies=[Depends(guard)])
    def material_review(ident: int, data: ReviewInput, current: Session = Depends(transaction)):
        review = service.review_material(current, ident, data)
        return {"id": review.id, "decision": review.decision, "material_id": ident}

    @router.get("/api/audit", dependencies=[Depends(guard)])
    def audit(response: Response, limit: int = Query(30, ge=1, le=100),
              offset: int = Query(0, ge=0), current: Session = Depends(session)):
        response.headers["X-Total-Count"] = str(current.scalar(select(func.count()).select_from(AdminAuditLog)) or 0)
        rows = current.scalars(select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).offset(offset).limit(limit)).all()
        return [present_audit(current, row) for row in rows]

    def attribution_view(row: AuthorAttribution, current: Session, *, detail: bool = False):
        # 此处的状态表示导入匹配途径，不代表历史人物身份已考证。
        path = row.source_path
        collection = ("唐诗作者资料" if path.endswith("authors.tang.json") else
                      "宋诗作者资料" if path.endswith("authors.song.json") else
                      "宋词作者资料" if path.endswith("author.song.json") else "其他作者资料")
        author = current.get(Author, row.author_id) if row.author_id else None
        data = {"id": row.id, "original_name": row.original_name,
                "author_id": row.author_id, "linked_author_name": author.canonical_name if author else None,
                "linked_author_dynasty": author.dynasty if author else None,
                "identity_label": ("已人工核对" if row.match_status == "reviewed" else "已关联 · 待核对") if author else "待考证",
                "collection": collection, "source_path": path, "source_index": row.source_index,
                "match_status": row.match_status}
        if detail:
            data.update(raw_biography=row.raw_biography, raw_short_biography=row.raw_short_biography,
                        original_id=row.original_id)
        return data

    @router.get("/api/attributions", dependencies=[Depends(guard)])
    def attributions(response: Response, q: str = Query("", max_length=80), limit: int = Query(30, ge=1, le=100),
                     offset: int = Query(0, ge=0), current: Session = Depends(session)):
        stmt = select(AuthorAttribution)
        if q.strip():
            stmt = stmt.where(AuthorAttribution.original_name.ilike(f"%{q.strip()}%"))
        response.headers["X-Total-Count"] = str(current.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = current.scalars(stmt.order_by(AuthorAttribution.id).offset(offset).limit(limit)).all()
        return [attribution_view(row, current) for row in rows]

    @router.get("/api/attributions/{ident}", dependencies=[Depends(guard)])
    def attribution_detail(ident: int, current: Session = Depends(session)):
        return attribution_view(service.require(current, AuthorAttribution, ident), current, detail=True)

    @router.put("/api/attributions/{ident}", dependencies=[Depends(guard)])
    def attribution_match(ident: int, author_id: int | None = None, current: Session = Depends(transaction)):
        row = service.bind_attribution(current, ident, author_id)
        return {"id": row.id, "author_id": row.author_id, "match_status": row.match_status}

    @router.get("/api/works/{ident}/dates", dependencies=[Depends(guard)])
    def work_dates(ident: int, current: Session = Depends(session)):
        service.require(current, Work, ident)
        rows = current.scalars(select(WorkDate).where(WorkDate.work_id == ident).order_by(WorkDate.year_start)).all()
        return [{"id": row.id, "year_start": row.year_start, "year_end": row.year_end,
                 "date_precision": row.date_precision, "confidence": row.confidence,
                 "rationale": row.rationale, "source_id": row.source_id,
                 "review_status": row.review_status, "is_preferred": row.is_preferred} for row in rows]

    @router.post("/api/works/{ident}/dates", dependencies=[Depends(guard)])
    def work_date_create(ident: int, data: WorkDateInput, current: Session = Depends(transaction)):
        row = service.save_work_date(current, ident, data)
        return {"id": row.id}

    @router.put("/api/works/{ident}/dates/{date_id}", dependencies=[Depends(guard)])
    def work_date_update(ident: int, date_id: int, data: WorkDateInput, current: Session = Depends(transaction)):
        row = service.save_work_date(current, ident, data, date_id)
        return {"id": row.id}

    @router.get("/api/authors/{ident}/events", dependencies=[Depends(guard)])
    def author_events(ident: int, current: Session = Depends(session)):
        service.require(current, Author, ident)
        rows = current.scalars(select(AuthorEvent).where(AuthorEvent.author_id == ident)
                               .order_by(AuthorEvent.year_start)).all()
        return [{"id": row.id, "year_start": row.year_start, "year_end": row.year_end,
                 "date_precision": row.date_precision, "event_label": row.event_label,
                 "source_id": row.source_id, "review_status": row.review_status} for row in rows]

    @router.post("/api/authors/{ident}/events", dependencies=[Depends(guard)])
    def author_event_create(ident: int, data: AuthorEventInput, current: Session = Depends(transaction)):
        row = service.save_author_event(current, ident, data)
        return {"id": row.id}

    @router.put("/api/authors/{ident}/events/{event_id}", dependencies=[Depends(guard)])
    def author_event_update(ident: int, event_id: int, data: AuthorEventInput, current: Session = Depends(transaction)):
        row = service.save_author_event(current, ident, data, event_id)
        return {"id": row.id}

    @router.delete("/api/works/{ident}/dates/{date_id}", dependencies=[Depends(guard)])
    def work_date_retire(ident: int, date_id: int, current: Session = Depends(transaction)):
        service.retire_work_date(current, ident, date_id)
        return {"id": date_id, "status": "rejected"}

    @router.delete("/api/authors/{ident}/events/{event_id}", dependencies=[Depends(guard)])
    def author_event_retire(ident: int, event_id: int, current: Session = Depends(transaction)):
        service.retire_author_event(current, ident, event_id)
        return {"id": event_id, "status": "rejected"}

    @router.post("/api/works/{ident}/pinyin", dependencies=[Depends(guard)])
    def pinyin_create(ident: int, data: PinyinInput, current: Session = Depends(transaction)):
        row = service.create_pinyin(current, ident, data)
        return {"id": row.id, "material_id": row.material_id}

    app.include_router(router)
