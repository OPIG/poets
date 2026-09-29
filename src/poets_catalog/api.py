"""只读 JSON API 与服务端页面的共同入口。"""
import os
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from .catalog import CatalogRepository, DEFAULT_PAGE_SIZE, FIELDS, GENRES, SearchIndexMissing
from .db import engine
from .site import register_site_routes
from .admin import register_admin


def create_app(db: Engine | None = None, mode: str | None = None, allow_test_client: bool = False) -> FastAPI:
    """将权利审核模式固定在进程启动时，预览模式只允许回环地址访问。"""
    selected_mode = mode or os.getenv("CATALOG_VIEW", "preview")
    if selected_mode not in {"preview", "public"}:
        raise ValueError("CATALOG_VIEW must be preview or public")
    database = db or engine()
    catalog = CatalogRepository(database, selected_mode)
    app = FastAPI(title="诗卷 · 诗词检索", version="0.2.0")

    @app.middleware("http")
    async def local_preview_only(request: Request, call_next):
        if selected_mode == "preview":
            client = request.client.host if request.client else ""
            host = request.url.hostname or ""
            allowed = {"127.0.0.1", "::1", "localhost"}
            if allow_test_client:
                allowed.add("testclient")
            if client not in allowed or host not in {"127.0.0.1", "::1", "localhost"}:
                return JSONResponse({"detail": "Private preview is available only on localhost"}, status_code=403)
        response = await call_next(request)
        # 预览与筛选结果不参与索引；JSON API 也不是搜索引擎的落地页。
        if selected_mode == "preview" or request.url.path.startswith(("/api/", "/search")):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.get("/api/meta")
    def metadata():
        return catalog.metadata()

    @app.get("/api/works")
    def works(q: str = Query("", max_length=80), field: str = "all", genre: str = "all",
              page: int = Query(1, ge=1, le=10000), size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=30)):
        if field not in FIELDS or genre not in {*GENRES, "all"}:
            raise HTTPException(422, "Unsupported field or genre")
        try:
            return catalog.search(q, field, genre, page, size)
        except SearchIndexMissing as exc:
            raise HTTPException(503, "Search index missing; run `poets-import reindex`") from exc

    @app.get("/api/works/{public_id}")
    def work_detail(public_id: UUID):
        item = catalog.detail(public_id)
        if item is None:
            raise HTTPException(404, "Work not found or not approved for publication")
        return item

    register_site_routes(app, catalog)
    register_admin(app, database)
    return app


app = create_app()
