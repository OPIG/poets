"""服务端渲染的阅读站：HTML 首屏、独立作品 URL、搜索页及受版权门禁约束的 SEO 入口。"""
import math
import os
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .catalog import CatalogRepository, FIELDS, GENRES, SearchIndexMissing

WEB_ROOT = Path(__file__).resolve().parent / "web"
TEMPLATES = Environment(loader=FileSystemLoader(WEB_ROOT / "templates"),
                        autoescape=select_autoescape(["html", "xml"]))
PAGE_SIZE = 12
SITEMAP_SIZE = 10_000


def public_base_url() -> str | None:
    """规范地址须由部署配置指定，不能信任任意请求 Host 头。"""
    url = os.getenv("SITE_URL", "").rstrip("/")
    return url if url.startswith("https://") else None


def search_url(q: str = "", field: str = "all", genre: str = "all", page: int = 1) -> str:
    """检索条件成为 URL，可复制、后退和无脚本翻页。"""
    params = {"q": q, "field": field, "genre": genre}
    if page > 1:
        params["page"] = page
    return "/search?" + urlencode(params)


def render(name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    """Jinja 自动转义原始作者、标题及正文，防止底本内容成为 HTML。"""
    return HTMLResponse(TEMPLATES.get_template(name).render(**context), status_code=status_code,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def register_site_routes(app: FastAPI, catalog: CatalogRepository) -> None:
    """将页面和 API 挂到同一 FastAPI 应用，共享本机限制及公开权利策略。"""
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    def context(request: Request, title: str, description: str, *, canonical_path: str | None = None):
        base = public_base_url() if catalog.mode == "public" else None
        return {"request": request, "title": title, "description": description,
                "mode": catalog.mode, "canonical": base + canonical_path if base and canonical_path else None,
                "robots": "index,follow" if base and canonical_path else "noindex,nofollow",
                "genres": GENRES, "search_fields": [
                    ("all", "综合检索"), ("author", "作者姓名"),
                    ("title", "诗词名称"), ("tag", "作品标签")],
                "search_url": search_url, "site_url": base}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        metadata = catalog.metadata()
        try:
            results = catalog.search(size=PAGE_SIZE)
        except SearchIndexMissing as exc:
            raise HTTPException(503, "请先运行 poets-import reindex") from exc
        data = context(request, "诗卷 · 中华诗词典藏", "浏览唐诗、宋诗与宋词，按作者、诗题和标签检索古典作品。",
                       canonical_path="/" if catalog.mode == "public" else None)
        data.update(metadata=metadata, results=results, q="", field="all", genre="all", page=1,
                    heading="全部作品 · 浏览馆藏", current_field="综合检索", current_genre="全部作品")
        return render("catalog.html", data)

    @app.get("/search", response_class=HTMLResponse, include_in_schema=False)
    def search(request: Request, q: str = "", field: str = "all", genre: str = "all", page: int = 1):
        if len(q) > 80 or field not in FIELDS or genre not in {*GENRES, "all"} or not 1 <= page <= 10000:
            raise HTTPException(422, "无效的检索条件")
        try:
            results = catalog.search(q=q, field=field, genre=genre, page=page, size=PAGE_SIZE)
        except SearchIndexMissing as exc:
            raise HTTPException(503, "请先运行 poets-import reindex") from exc
        metadata = catalog.metadata()
        current_genre = GENRES.get(genre, "全部作品")
        current_field = dict(context(request, "", "")["search_fields"])[field]
        heading = f"“{q.strip()}” 的检索结果" if q.strip() else f"{current_genre} · 浏览馆藏"
        data = context(request, f"{heading} · 诗卷", "按作者、诗词名、标签和类别查找古典诗词。")
        data.update(metadata=metadata, results=results, q=q, field=field, genre=genre, page=page,
                    heading=heading, current_field=current_field, current_genre=current_genre)
        return render("catalog.html", data)

    @app.get("/works/{public_id}", response_class=HTMLResponse, include_in_schema=False)
    def detail(request: Request, public_id: UUID):
        item = catalog.detail(public_id)
        if item is None:
            raise HTTPException(404, "作品不存在或尚未获准公开")
        category = GENRES[item["genre"]]
        teaser = " ".join(item["paragraphs"][:2])[:115]
        data = context(request, f"{item['title']} - {item['author']} · 诗卷",
                       f"{item['author']}《{item['title']}》原文。{teaser}",
                       canonical_path=f"/works/{public_id}" if catalog.mode == "public" else None)
        data.update(item=item, category=category,
                    json_ld={"@context": "https://schema.org", "@type": "CreativeWork",
                                        "name": item["title"], "author": {"@type": "Person", "name": item["author"]},
                                        "inLanguage": "zh", "url": data["canonical"]}
                    if data["canonical"] else None)
        return render("work.html", data)

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots():
        base = public_base_url() if catalog.mode == "public" else None
        if not base:
            return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"Cache-Control": "no-store"})
        return PlainTextResponse(f"User-agent: *\nDisallow: /api/\nDisallow: /search\nSitemap: {base}/sitemap.xml\n",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap():
        base = public_base_url() if catalog.mode == "public" else None
        if not base:
            raise HTTPException(404)
        total = catalog.metadata()["total"]
        links = [f"<sitemap><loc>{base}/sitemaps/works-{n}.xml</loc></sitemap>"
                 for n in range(1, math.ceil(total / SITEMAP_SIZE) + 1)]
        links.insert(0, f"<sitemap><loc>{base}/sitemaps/home.xml</loc></sitemap>")
        xml = '<?xml version="1.0" encoding="UTF-8"?>' + \
              '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(links) + '</sitemapindex>'
        return Response(xml, media_type="application/xml", headers={"Cache-Control": "no-store"})

    @app.get("/sitemaps/home.xml", include_in_schema=False)
    def home_sitemap():
        base = public_base_url() if catalog.mode == "public" else None
        if not base:
            raise HTTPException(404)
        xml = '<?xml version="1.0" encoding="UTF-8"?>' + \
              f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{base}/</loc></url></urlset>'
        return Response(xml, media_type="application/xml", headers={"Cache-Control": "no-store"})

    @app.get("/sitemaps/works-{page}.xml", include_in_schema=False)
    def works_sitemap(page: int):
        base = public_base_url() if catalog.mode == "public" else None
        if not base or page < 1 or page > math.ceil(catalog.metadata()["total"] / SITEMAP_SIZE):
            raise HTTPException(404)
        ids = catalog.public_ids((page - 1) * SITEMAP_SIZE, SITEMAP_SIZE)
        links = ''.join(f"<url><loc>{base}/works/{ident}</loc></url>" for ident in ids)
        xml = '<?xml version="1.0" encoding="UTF-8"?>' + \
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + links + '</urlset>'
        return Response(xml, media_type="application/xml", headers={"Cache-Control": "no-store"})
