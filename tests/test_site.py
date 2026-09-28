"""服务端渲染、SEO 元数据和权利门禁的回归测试。"""
import os
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, insert

from poets_catalog.api import create_app
from poets_catalog.db import engine
from poets_catalog.importer import load, reindex
from poets_catalog.models import Material, RightsReview, Work, WorkVersion

FIXTURE = Path(__file__).parent / "fixtures" / "v1"


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="需要独立的 PostgreSQL 测试库")
def test_preview_html_contains_server_rendered_content_and_noindex(monkeypatch):
    """无 JavaScript 的请求直接获得正文、分页链接与禁止索引标记。"""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    load(FIXTURE, ["tang"], "ssr-fixture")
    reindex()
    with TestClient(create_app(engine(), mode="preview", allow_test_client=True), base_url="http://localhost") as client:
        response = client.get("/search", params={"q": "乙", "field": "author", "genre": "tang_poem"})
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert '<meta name="robots" content="noindex,nofollow">' in response.text
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "春日" in response.text and "/works/" in response.text
        assert 'name="genre" value="tang_poem"' in response.text
        assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"
        assert client.get("/sitemap.xml").status_code == 404
        assert client.get("/search?field=bad").status_code == 422
        assert client.get("/search?page=0").status_code == 422
        item = client.get("/api/works", params={"q": "春日", "field": "title"}).json()["items"][0]
        work = client.get("/works/" + item["id"])
        assert work.status_code == 200
        assert "春風吹柳。" in work.text
        assert "<h1" in work.text
        assert 'application/ld+json' not in work.text
        assert client.get("/works/00000000-0000-0000-0000-000000000000").status_code == 404


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="需要独立的 PostgreSQL 测试库")
def test_public_html_only_indexes_approved_work(monkeypatch):
    """首页与 sitemap 不得收录未授权原文，撤回审核后立即消失。"""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("SITE_URL", "https://poetry.example")
    load(FIXTURE, ["tang"], "ssr-fixture")
    db = engine()
    with db.connect() as connection:
        transaction = connection.begin()
        try:
            with TestClient(create_app(connection, mode="public", allow_test_client=True), base_url="http://localhost") as client:
                empty = client.get("/")
                assert empty.status_code == 200
                assert "春風吹柳。" not in empty.text
                assert "春日" not in empty.text
                assert client.get("/sitemaps/works-1.xml").status_code == 404
                work = connection.execute(select(Work.public_id, WorkVersion.material_id, WorkVersion.content_hash)
                    .join(WorkVersion, WorkVersion.work_id == Work.id)
                    .where(Work.source_lookup_key == "tang_poem:work-1", WorkVersion.is_current.is_(True))).one()
                assert client.get(f"/works/{work.public_id}").status_code == 404
                connection.execute(update(Material).where(Material.id == work.material_id).values(workflow_status="published"))
                review = connection.scalar(insert(RightsReview).values(
                    material_id=work.material_id, decision="approved", legal_basis="测试授权",
                    permitted_scope="web", evidence_uri="test://evidence", reviewer="tester",
                    reviewed_hash=work.content_hash).returning(RightsReview.id))
                detail = client.get(f"/works/{work.public_id}")
                assert detail.status_code == 200
                assert '<link rel="canonical" href="https://poetry.example/works/' in detail.text
                assert 'application/ld+json' in detail.text
                assert "春風吹柳。" in detail.text
                listing = client.get("/")
                assert f'/works/{work.public_id}' in listing.text
                assert "春日" in listing.text
                assert 'noindex,nofollow' not in listing.text
                assert "Sitemap: https://poetry.example/sitemap.xml" in client.get("/robots.txt").text
                assert f"/works/{work.public_id}" in client.get("/sitemaps/works-1.xml").text
                assert '<meta name="robots" content="noindex,nofollow">' in client.get("/search?q=春日").text
                connection.execute(update(RightsReview).where(RightsReview.id == review).values(decision="revoked"))
                assert client.get(f"/works/{work.public_id}").status_code == 404
                assert client.get("/sitemaps/works-1.xml").status_code == 404
        finally:
            transaction.rollback()
    db.dispose()

@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="需要独立的 PostgreSQL 测试库")
def test_public_mode_without_site_url_stays_noindex(monkeypatch):
    """未配置可信域名时不根据 Host 伪造 canonical 或 sitemap。"""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.delenv("SITE_URL", raising=False)
    load(FIXTURE, ["tang"], "ssr-fixture")
    reindex()
    with TestClient(create_app(engine(), mode="public"), base_url="http://localhost") as client:
        response = client.get("/", headers={"host": "localhost"})
        assert response.status_code == 200
        assert '<meta name="robots" content="noindex,nofollow">' in response.text
        assert 'rel="canonical"' not in response.text
        assert client.get("/sitemap.xml").status_code == 404
