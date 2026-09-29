"""只读检索端到端测试：字段搜索、正文和预览隔离。"""
import os
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from poets_catalog.api import create_app
from poets_catalog.db import engine
from poets_catalog.importer import load, reindex

FIXTURE = Path(__file__).parent / "fixtures" / "v1"


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_search_fields_and_detail(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    load(FIXTURE, ["tang"], "api-fixture")
    reindex()
    with TestClient(create_app(engine(), mode="preview", allow_test_client=True), base_url="http://localhost") as client:
        for field, term in (("author", "乙"), ("title", "春日"), ("tag", "西湖"), ("tag", "苏轼")):
            response = client.get("/api/works", params={"q": term, "field": field, "genre": "tang_poem"})
            assert response.status_code == 200
            assert response.json()["total"] >= 1
            assert response.json()["size"] == 20
        first = client.get("/api/works", params={"q": "春日", "field": "title"}).json()["items"][0]
        detail = client.get(f"/api/works/{first['id']}").json()
        assert detail["paragraphs"] == ["春風吹柳。", "明月照人。"]
        assert detail["author"] == "乙"
        assert detail["title"] == "春日"
        assert detail["tags"] == ["西湖", "蘇軾"]
        assert detail["source"] == "chinese-poetry"
        assert detail["source_url"] == "https://github.com/chinese-poetry/chinese-poetry"
        assert detail["source_file_url"].startswith(
            "https://github.com/chinese-poetry/chinese-poetry/blob/")
        assert detail["source_file_url"].endswith("/%E5%85%A8%E5%94%90%E8%AF%97/poet.tang.0.json")
        assert "source_path" not in detail
        assert client.get("/api/works", params={"q": "%", "field": "title"}).json()["total"] == 0
        assert client.get("/api/works", params={"field": "invalid"}).status_code == 422
        assert client.get("/api/works", params={"size": 1000}).status_code == 422
        assert client.get("/api/works/not-a-uuid").status_code == 422


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL test database")
def test_public_mode_hides_unapproved_and_preview_is_local(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    load(FIXTURE, ["tang"], "api-fixture")
    with TestClient(create_app(engine(), mode="public"), base_url="http://localhost") as client:
        assert client.get("/api/works").json()["total"] == 0
    with TestClient(create_app(engine(), mode="preview", allow_test_client=True), base_url="http://example.com") as client:
        assert client.get("/api/works").status_code == 403
