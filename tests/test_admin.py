"""后台鉴权、写入版本、撤下和权利审核的隔离测试。"""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update

from poets_catalog.api import create_app
from poets_catalog.db import engine
from poets_catalog.models import AdminAuditLog, WorkVersion, AdminSession, AdminUser
from poets_catalog.admin.auth import create_user, set_password, sha256

PASSWORD = "Test-password-12-characters!"
CSRF = ""


@pytest.fixture
def admin(monkeypatch):
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("需要独立的 PostgreSQL 测试库")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_ALLOW_REMOTE", raising=False)
    db = engine()
    with db.connect() as conn:
        outer = conn.begin()
        try:
            username = "admin_" + uuid4().hex[:12]
            create_user(conn, username, PASSWORD)
            with TestClient(create_app(conn, mode="public"), base_url="http://localhost") as client:
                global CSRF
                login = client.post("/admin/api/login", json={"username":username,"password":PASSWORD})
                assert login.status_code == 200, login.text
                CSRF = login.json()["csrf_token"]
                yield client, conn
        finally:
            outer.rollback()
    db.dispose()


def auth():
    return {"X-CSRF-Token": CSRF}


def test_login_page_has_no_default_credentials(monkeypatch):
    """后台登录页可访问，但未登录不能访问管理数据。"""
    with TestClient(create_app(mode="public"), base_url="http://localhost") as client:
        assert client.get("/admin/api/overview").status_code == 401
        assert client.get("/admin").status_code == 200


def test_authentication_and_noindex(admin):
    client, _ = admin
    assert client.get("/admin/api/overview").status_code == 200
    assert client.post("/admin/api/works", json={}).status_code == 403
    assert client.delete("/admin/api/works/1").status_code == 403
    assert client.get("/admin/api/overview", headers={"Origin": "https://evil.example"}).status_code == 200
    assert client.post("/admin/api/works", json={}, headers={**auth(), "Origin": "https://evil.example"}).status_code == 403
    page = client.get("/admin")
    assert page.status_code == 200
    assert page.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert PASSWORD not in page.text
    restored = client.get("/admin/api/session")
    assert restored.status_code == 200
    assert restored.json()["csrf_token"] == CSRF


def test_work_lifecycle_and_rights_review(admin):
    """新增、修订、归档、恢复与批准均不得覆盖旧版或绕过审核。"""
    client, conn = admin
    body = {"genre": "tang_poem", "author_name": "测试作者", "title": "测试之作",
            "paragraphs": ["第一句。", "第二句。"], "tags": ["测试"]}
    created = client.post("/admin/api/works", json=body, headers=auth())
    assert created.status_code == 200, created.text
    work_id, public_id = created.json()["id"], created.json()["public_id"]
    assert client.get(f"/works/{public_id}").status_code == 404
    detail = client.get(f"/admin/api/works/{work_id}", headers=auth()).json()
    assert detail["versions"][0]["paragraphs"] == body["paragraphs"]
    material = detail["versions"][0]["material_id"]
    bad = client.post(f"/admin/api/materials/{material}/reviews", json={"decision": "approved"}, headers=auth())
    assert bad.status_code == 422
    review = {"decision": "approved", "legal_basis": "经核权的原创测试文本",
              "permitted_scope": "web", "evidence_uri": "test://proof"}
    assert client.post(f"/admin/api/materials/{material}/reviews", json=review, headers=auth()).status_code == 200
    assert "第一句。" in client.get(f"/works/{public_id}").text
    assert client.get(f"/api/works/{public_id}").status_code == 200
    revision = {**body, "paragraphs": ["新版本原文。"]}
    assert client.put(f"/admin/api/works/{work_id}", json=revision, headers=auth()).status_code == 200
    assert client.get(f"/works/{public_id}").status_code == 404
    detail = client.get(f"/admin/api/works/{work_id}", headers=auth()).json()
    assert len(detail["versions"]) == 2
    assert detail["versions"][0]["paragraphs"] == ["新版本原文。"]
    assert detail["versions"][1]["paragraphs"] == body["paragraphs"]
    new_material = detail["versions"][0]["material_id"]
    assert client.post(f"/admin/api/materials/{new_material}/reviews", json=review, headers=auth()).status_code == 200
    assert "新版本原文。" in client.get(f"/works/{public_id}").text
    assert client.delete(f"/admin/api/works/{work_id}", headers=auth()).status_code == 200
    assert client.get(f"/works/{public_id}").status_code == 404
    assert client.post(f"/admin/api/works/{work_id}/restore", headers=auth()).status_code == 200
    assert client.get(f"/works/{public_id}").status_code == 404
    assert client.post(f"/admin/api/materials/{new_material}/reviews", json=review, headers=auth()).status_code == 200
    assert client.get(f"/works/{public_id}").status_code == 200
    assert client.post(f"/admin/api/materials/{new_material}/reviews", json={"decision": "revoked"}, headers=auth()).status_code == 200
    assert client.get(f"/works/{public_id}").status_code == 404
    assert conn.scalar(select(func.count()).select_from(WorkVersion).where(WorkVersion.work_id == work_id)) == 2
    assert conn.scalar(select(func.count()).select_from(AdminAuditLog).where(AdminAuditLog.entity_id == work_id)) >= 4


def test_author_and_editorial_materials(admin):
    """作者、简介、译文和赏析在独立材料审核前不得公开。"""
    client, _ = admin
    author = client.post("/admin/api/authors", json={"canonical_name": "新作者", "dynasty": "song"}, headers=auth())
    assert author.status_code == 200
    author_id = author.json()["id"]
    bio = client.post(f"/admin/api/authors/{author_id}/biographies", json={"body": "原创小传"}, headers=auth())
    assert bio.status_code == 200
    source = client.post("/admin/api/sources", json={"key": f"test:{uuid4()}", "kind": "book", "title": "测试参考资料"}, headers=auth())
    assert source.status_code == 200
    work = client.post("/admin/api/works", json={"genre": "song_ci", "author_name": "新作者",
        "author_id": author_id, "rhythmic": "蝶恋花", "paragraphs": ["原文。"]}, headers=auth()).json()
    item = client.post(f"/admin/api/works/{work['id']}/commentaries", json={"body": "赏析。"}, headers=auth())
    translation = client.post(f"/admin/api/works/{work['id']}/translations", json={
        "language_tag": "en", "blocks": ["Translated text."]}, headers=auth())
    assert item.status_code == 200 and translation.status_code == 200
    assert client.get(f"/admin/api/materials/{bio.json()['material_id']}", headers=auth()).json()["body"] == "原创小传"
    withdrawn_id = translation.json()["material_id"]
    assert client.delete(f"/admin/api/materials/{withdrawn_id}", headers=auth()).status_code == 200
    assert client.post(f"/admin/api/materials/{withdrawn_id}/reviews", json={"decision":"revoked"}, headers=auth()).status_code == 200
    assert client.get(f"/admin/api/materials/{withdrawn_id}",headers=auth()).json()["status"] == "withdrawn"
    assert client.delete(f"/admin/api/authors/{author_id}", headers=auth()).status_code == 200
    assert client.get(f"/admin/api/audit", headers=auth()).status_code == 200


def test_chronology_pinyin_and_disambiguation(admin):
    """时间线记录可增改；拼音必须逐字对齐；同名署名只能手动关联。"""
    client, conn = admin
    author = client.post("/admin/api/authors", json={"canonical_name":"时间作者", "dynasty":"tang"}, headers=auth()).json()
    work = client.post("/admin/api/works", json={"genre":"tang_poem", "author_name":"时间作者",
        "author_id":author["id"], "title":"月夜", "paragraphs":["明月。"]}, headers=auth()).json()
    date = {"year_start":760,"year_end":762,"date_precision":"range",
            "confidence":"high","review_status":"reviewed","is_preferred":True}
    created = client.post(f"/admin/api/works/{work['id']}/dates", json=date, headers=auth())
    assert created.status_code == 200
    assert client.get(f"/admin/api/works/{work['id']}/dates",headers=auth()).json()[0]["year_start"] == 760
    assert client.put(f"/admin/api/works/{work['id']}/dates/{created.json()['id']}", json={**date,"year_start":761},headers=auth()).status_code == 200
    event = {"year_start":750,"year_end":751,"date_precision":"range","event_label":"客居某地"}
    saved = client.post(f"/admin/api/authors/{author['id']}/events",json=event,headers=auth())
    assert saved.status_code == 200
    assert client.get(f"/admin/api/authors/{author['id']}/events",headers=auth()).json()[0]["event_label"] == "客居某地"
    assert client.put(f"/admin/api/authors/{author['id']}/events/{saved.json()['id']}",json={**event,"event_label":"迁居"},headers=auth()).status_code == 200
    assert client.delete(f"/admin/api/authors/{author['id']}/events/{saved.json()['id']}",headers=auth()).status_code == 200
    assert client.delete(f"/admin/api/works/{work['id']}/dates/{created.json()['id']}",headers=auth()).status_code == 200
    bad = {"tokens":[{"paragraph_index":0,"char_index":0,"character":"错","pinyin":"cuò"}]}
    assert client.post(f"/admin/api/works/{work['id']}/pinyin",json=bad,headers=auth()).status_code == 422
    good = {"tokens":[{"paragraph_index":0,"char_index":0,"character":"明","pinyin":"míng"}]}
    assert client.post(f"/admin/api/works/{work['id']}/pinyin",json=good,headers=auth()).status_code == 200
    attribution = client.get("/admin/api/attributions",params={"q":"甲"},headers=auth()).json()
    if attribution:
        ident = attribution[0]["id"]
        linked = client.put(f"/admin/api/attributions/{ident}",params={"author_id":author["id"]},headers=auth())
        assert linked.status_code == 200 and linked.json()["match_status"] == "reviewed"


def test_admin_only_over_https_for_remote_and_source_snapshot_is_immutable(admin, monkeypatch):
    client, conn = admin
    source = client.get("/admin/api/sources",headers=auth()).json()
    snapshot = next((item for item in source if item["kind"]=="repository_snapshot"),None)
    if snapshot:
        assert client.put(f"/admin/api/sources/{snapshot['id']}", json={
            "key":snapshot["key"],"kind":"repository_snapshot","title":"改写来源"},headers=auth()).status_code == 409
    monkeypatch.setenv("ADMIN_ALLOW_REMOTE","1")
    monkeypatch.delenv("SITE_URL",raising=False)
    with pytest.raises(RuntimeError,match="HTTPS"):
        create_app(conn,mode="public")


def test_session_expiry_logout_and_password_reset(admin):
    """HttpOnly 会话过期/退出/重置密码均立即失效。"""
    from datetime import datetime, timedelta, timezone
    client, conn = admin
    cookie = client.cookies.get("poets_admin_session")
    assert cookie
    session = conn.execute(select(AdminSession.id, AdminSession.user_id, AdminSession.csrf_token,
        AdminSession.token_hash).where(AdminSession.token_hash == sha256(cookie))).one()
    assert session.csrf_token == CSRF
    assert session.token_hash != cookie
    conn.execute(update(AdminSession).where(AdminSession.id == session.id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert client.get("/admin/api/overview").status_code == 401
    # 用新登录流程单独验证退出和密码重置。
    username = conn.scalar(select(AdminUser.username).where(AdminUser.id == session.user_id))
    relogin = client.post("/admin/api/login", json={"username": username, "password": PASSWORD})
    assert relogin.status_code == 200
    new_csrf = relogin.json()["csrf_token"]
    assert client.post("/admin/api/logout", headers={"X-CSRF-Token": new_csrf}).status_code == 200
    assert client.get("/admin/api/overview").status_code == 401
    login = client.post("/admin/api/login", json={"username": username, "password": PASSWORD})
    assert login.status_code == 200
    set_password(conn, username, "A-new-long-password-123")
    assert client.get("/admin/api/overview").status_code == 401
    assert client.post("/admin/api/login", json={"username": username, "password": PASSWORD}).status_code == 401
    assert client.post("/admin/api/login", json={"username": username, "password": "A-new-long-password-123"}).status_code == 200


def test_account_lockout_after_failed_password(admin):
    """连续五次失败暂时锁定账号，避免无限猜测密码。"""
    from datetime import datetime, timezone
    client, conn = admin
    cookie = client.cookies.get("poets_admin_session")
    user_id = conn.scalar(select(AdminSession.user_id).where(AdminSession.token_hash == sha256(cookie)))
    username = conn.scalar(select(AdminUser.username).where(AdminUser.id == user_id))
    for _ in range(5):
        assert client.post("/admin/api/login", json={"username": username, "password": "incorrect"}).status_code == 401
    locked_until = conn.scalar(select(AdminUser.locked_until).where(AdminUser.username == username))
    assert locked_until > datetime.now(timezone.utc)
    assert client.post("/admin/api/login", json={"username": username, "password": PASSWORD}).status_code == 401


def test_session_refresh_and_cookie_security(admin):
    """刷新后通过 HttpOnly Cookie 恢复会话；没有 CSRF 不允许写入。"""
    client, _ = admin
    restored = client.get("/admin/api/session")
    assert restored.status_code == 200
    assert restored.json()["csrf_token"] == CSRF
    assert client.post("/admin/api/authors", json={"canonical_name":"测试", "dynasty":"tang"}).status_code == 403
    assert client.post("/admin/api/authors", json={"canonical_name":"测试", "dynasty":"tang"},
                       headers={"X-CSRF-Token":CSRF}).status_code == 200


def test_remote_session_cookie_requires_https(admin, monkeypatch):
    """允许远程部署时设置 Secure Cookie，且登录不接受跨域来源。"""
    from poets_catalog.admin.auth import create_user
    _, conn = admin
    username = "remote_" + uuid4().hex[:12]
    create_user(conn, username, PASSWORD)
    monkeypatch.setenv("ADMIN_ALLOW_REMOTE", "1")
    monkeypatch.setenv("SITE_URL", "https://poetry.example")
    with TestClient(create_app(conn, mode="public"), base_url="https://poetry.example") as client:
        rejected = client.post("/admin/api/login", json={"username":username,"password":PASSWORD},
                               headers={"Origin":"https://other.example"})
        assert rejected.status_code == 403
        response = client.post("/admin/api/login", json={"username":username,"password":PASSWORD},
                               headers={"Origin":"https://poetry.example"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        for flag in ("httponly", "secure", "samesite=strict", "path=/admin"):
            assert flag in cookie.lower()
        assert client.get("/admin/api/session").status_code == 200


def test_list_pagination_metadata(admin):
    """各管理列表给出过滤后的总数，前端才能计算总页数与跳页边界。"""
    client, _ = admin
    for path in ("works", "authors", "attributions", "materials", "sources", "audit"):
        params = {"limit": 2, "offset": 0}
        if path == "materials":
            params["status"] = "all"
        response = client.get(f"/admin/api/{path}", params=params)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert response.headers["X-Total-Count"].isdigit()
        assert int(response.headers["X-Total-Count"]) >= len(response.json())
    # 过滤条件改变后数量也必须跟着改变，而不是返回总库数量。
    matching = client.get("/admin/api/works", params={"q": "不存在的作品_1"})
    assert matching.headers["X-Total-Count"] == "0"
    assert matching.json() == []
    # 分页后的响应头仍表示整组筛选结果，不是本页条数。
    works_first = client.get("/admin/api/works", params={"limit": 1, "offset": 0})
    works_second = client.get("/admin/api/works", params={"limit": 1, "offset": 1})
    assert works_first.headers["X-Total-Count"] == works_second.headers["X-Total-Count"]
    assert works_first.json()[0]["id"] != works_second.json()[0]["id"]
    assert client.get("/admin/api/sources", params={"q": "不存在的来源_1"}).headers["X-Total-Count"] == "0"
    assert client.get("/admin/api/authors", params={"q": "不存在的作者_1"}).headers["X-Total-Count"] == "0"
    bad_status = client.get("/admin/api/materials", params={"status": "invalid"})
    assert bad_status.status_code == 422


def test_review_list_identifies_work_and_author(admin):
    """审核列表应展示作品、作者及出处，而不是只有 original 和摘要。"""
    client, _ = admin
    created = client.post('/admin/api/works', json={
        'genre': 'tang_poem', 'author_name': '测试诗人', 'title': '月下试作',
        'paragraphs': ['明月在前。']}, headers=auth())
    assert created.status_code == 200
    material = client.get(f"/admin/api/works/{created.json()['id']}", headers=auth()).json()['versions'][0]['material_id']
    rows = client.get('/admin/api/materials', params={'status': 'staged', 'limit': 30}, headers=auth()).json()
    row = next(r for r in rows if r['id'] == material)
    assert row['kind'] == 'original'
    assert row['display_title'] == '月下试作'
    assert row['creator'] == '测试诗人'
    assert row['source_title'] == '诗卷人工编辑'
    assert row['related_work_id'] == created.json()['id']


def test_approval_without_evidence_remains_staged(admin):
    """审核缺证据不能发布，后台应收到可读的校验错误。"""
    client, _ = admin
    created = client.post('/admin/api/works', json={
        'genre': 'song_ci', 'author_name': '测试词人', 'rhythmic': '清平乐',
        'paragraphs': ['原文。']}, headers=auth())
    ident = client.get(f"/admin/api/works/{created.json()['id']}", headers=auth()).json()['versions'][0]['material_id']
    response = client.post(f'/admin/api/materials/{ident}/reviews', json={'decision': 'approved'}, headers=auth())
    assert response.status_code == 422
    assert '权利依据' in str(response.json())
    assert client.get(f'/admin/api/materials/{ident}', headers=auth()).json()['status'] == 'staged'


def test_work_revision_cannot_change_person_link(admin):
    """普通原文修订不能顺手把作品改挂到另一个规范人物。"""
    client, _ = admin
    author_a = client.post('/admin/api/authors', json={'canonical_name': '甲作者', 'dynasty': 'tang'}, headers=auth()).json()['id']
    author_b = client.post('/admin/api/authors', json={'canonical_name': '乙作者', 'dynasty': 'tang'}, headers=auth()).json()['id']
    payload = {'genre': 'tang_poem', 'author_name': '甲作者', 'author_id': author_a,
               'title': '原题', 'paragraphs': ['原文。']}
    work_id = client.post('/admin/api/works', json=payload, headers=auth()).json()['id']
    changed = client.put(f'/admin/api/works/{work_id}', json={**payload, 'author_id': author_b}, headers=auth())
    assert changed.status_code == 422
    assert client.get(f'/admin/api/works/{work_id}', headers=auth()).json()['author_id'] == author_a
    revision = client.put(f'/admin/api/works/{work_id}', json={
        **payload, 'author_id': None, 'paragraphs': ['修订原文。']}, headers=auth())
    assert revision.status_code == 200
    assert client.get(f'/admin/api/works/{work_id}', headers=auth()).json()['author_id'] == author_a


def test_audit_entries_have_readable_context(admin):
    """审核操作记录可直接看出动作、目标和操作者，原始编号仍保留供追溯。"""
    client, _ = admin
    work = client.post('/admin/api/works', json={
        'genre': 'tang_poem', 'author_name': '乙', 'title': '明月之作', 'paragraphs': ['月明。']}, headers=auth()).json()
    material_id = client.get(f'/admin/api/works/{work["id"]}', headers=auth()).json()['versions'][0]['material_id']
    response = client.post(f'/admin/api/materials/{material_id}/reviews', json={
        'decision': 'approved', 'legal_basis': '测试原创', 'permitted_scope': 'web',
        'evidence_uri': 'test://evidence'}, headers=auth())
    assert response.status_code == 200
    row = next(item for item in client.get('/admin/api/audit', headers=auth()).json()
               if item['entity_type'] == 'material' and item['entity_id'] == material_id)
    assert row['action_label'] == '批准公开'
    assert '明月之作' in row['target_label']
    assert '乙' in row['target_label']
    assert row['actor'] != 'unknown'
    assert row['entity_id'] == material_id


def test_approval_scope_is_explicit_web_only(admin):
    """内部使用或任意文字不能被当成公开展示许可。"""
    client, _ = admin
    created = client.post('/admin/api/works', json={
        'genre': 'tang_poem', 'author_name': '测试作者', 'title': '未授权网页展示',
        'paragraphs': ['测试正文。']}, headers=auth()).json()
    material_id = client.get(f'/admin/api/works/{created["id"]}', headers=auth()).json()['versions'][0]['material_id']
    response = client.post(f'/admin/api/materials/{material_id}/reviews', json={
        'decision': 'approved', 'legal_basis': '仅内部整理', 'permitted_scope': 'internal',
        'evidence_uri': 'test://internal'}, headers=auth())
    assert response.status_code == 422
    assert client.get(f'/admin/api/materials/{material_id}', headers=auth()).json()['status'] == 'staged'
    assert client.get(f'/works/{created["public_id"]}').status_code == 404


def test_attribution_list_uses_readable_source_and_identity(admin):
    """署名列表的主信息应是作品集与关联状态，而非导入状态码/JSON 路径。"""
    client, _ = admin
    rows = client.get('/admin/api/attributions', params={'q': '甲'}, headers=auth()).json()
    assert rows
    row = next(x for x in rows if x['source_path'].startswith('全唐诗/'))
    assert row['collection'] == '唐诗作者资料'
    assert row['identity_label'] == '已关联 · 待核对'
    assert row['linked_author_name'] == '甲'
    assert row['source_path'].endswith('authors.tang.json')
    detail = client.get(f'/admin/api/attributions/{row["id"]}', headers=auth()).json()
    assert detail['collection'] == row['collection']
    assert 'raw_biography' in detail


def test_attribution_unlinked_label_and_search_candidates(admin):
    client, _ = admin
    rows = client.get('/admin/api/attributions', params={'q': '蔡'}, headers=auth()).json()
    if rows:
        assert any(x['identity_label'] == '待考证' and x['linked_author_name'] is None for x in rows)
    authors = client.get('/admin/api/authors', params={'q': '甲', 'limit': 10}, headers=auth()).json()
    assert authors
    assert all('id' in x and 'name' in x and 'dynasty' in x for x in authors)


def test_attribution_binding_changes_only_relationship(admin):
    """人工关联只改变外键与核对状态，不改规范人物主键和原始署名。"""
    client, conn = admin
    row = client.get('/admin/api/attributions', params={'q':'甲'}, headers=auth()).json()[0]
    target = client.get('/admin/api/authors', params={'q':'乙'}, headers=auth()).json()[0]
    before = client.get(f'/admin/api/attributions/{row["id"]}', headers=auth()).json()
    response = client.put(f'/admin/api/attributions/{row["id"]}',
                          params={'author_id':target['id']}, headers=auth())
    assert response.status_code == 200
    after = client.get(f'/admin/api/attributions/{row["id"]}', headers=auth()).json()
    assert after['identity_label'] == '已人工核对'
    assert after['linked_author_name'] == target['name']
    assert after['original_name'] == before['original_name']
    assert after['source_path'] == before['source_path']
    assert client.get(f'/admin/api/authors/{target["id"]}', headers=auth()).json()['public_id'] == target['public_id']


def test_archived_work_hidden_from_preview_but_retained_in_admin(admin):
    """归档是从阅读界面撤下，不是物理删除；恢复仍保留原始版本。"""
    client, conn = admin
    from poets_catalog.catalog import CatalogRepository
    created = client.post('/admin/api/works', json={
        'genre': 'song_poem', 'author_name': '测试诗人', 'title': '归档示例',
        'paragraphs': ['保留的原文。']}, headers=auth()).json()
    preview = CatalogRepository(conn, 'preview')
    assert preview.detail(__import__('uuid').UUID(created['public_id'])) is not None
    assert client.delete(f'/admin/api/works/{created["id"]}', headers=auth()).status_code == 200
    assert preview.detail(__import__('uuid').UUID(created['public_id'])) is None
    assert client.get(f'/admin/api/works/{created["id"]}', headers=auth()).status_code == 200
    assert client.post(f'/admin/api/works/{created["id"]}/restore', headers=auth()).status_code == 200
    assert preview.detail(__import__('uuid').UUID(created['public_id'])) is not None


def test_archived_filters_are_combined_with_search_and_count(admin):
    """归档状态筛选应与关键词联动，并提供筛选后的准确分页总数。"""
    client, _ = admin
    author = client.post('/admin/api/authors', json={
        'canonical_name': '筛选归档作者', 'dynasty': 'tang'}, headers=auth()).json()
    author_id = author['id']
    work = client.post('/admin/api/works', json={
        'genre': 'tang_poem', 'author_name': '筛选归档作者', 'title': '筛选归档作品',
        'paragraphs': ['临时测试原文。']}, headers=auth()).json()
    work_id = work['id']
    assert client.delete(f'/admin/api/authors/{author_id}', headers=auth()).status_code == 200
    assert client.delete(f'/admin/api/works/{work_id}', headers=auth()).status_code == 200
    for route, ident in (('authors', author_id), ('works', work_id)):
        response = client.get(f'/admin/api/{route}', params={
            'q': '筛选归档', 'status': 'archived', 'limit': 1, 'offset': 0}, headers=auth())
        assert response.status_code == 200
        assert response.headers['X-Total-Count'] == '1'
        assert [row['id'] for row in response.json()] == [ident]
        normal = client.get(f'/admin/api/{route}', params={
            'q': '筛选归档', 'status': 'normal'}, headers=auth())
        assert normal.status_code == 200
        assert normal.headers['X-Total-Count'] == '0'
        assert normal.json() == []
        invalid = client.get(f'/admin/api/{route}', params={
            'status': 'invalid'}, headers=auth())
        assert invalid.status_code == 422


def test_archived_author_restores_with_explicit_action(admin):
    """归档作者只能执行恢复动作，不应重复归档或改动历史身份主键。"""
    client, _ = admin
    created = client.post('/admin/api/authors', json={
        'canonical_name': '待恢复作者', 'dynasty': 'song'}, headers=auth()).json()
    author_id = created['id']
    assert client.delete(f'/admin/api/authors/{author_id}', headers=auth()).status_code == 200
    assert client.delete(f'/admin/api/authors/{author_id}', headers=auth()).status_code == 409
    restored = client.post(f'/admin/api/authors/{author_id}/restore', headers=auth())
    assert restored.status_code == 200
    assert restored.json()['status'] == 'unverified'
    assert client.get(f'/admin/api/authors/{author_id}', headers=auth()).json()['public_id'] == created['public_id']
    assert client.post(f'/admin/api/authors/{author_id}/restore', headers=auth()).status_code == 409


def test_author_biography_revision_keeps_original_and_requires_separate_withdrawal(admin):
    """修订来源小传只追加待审版本，旧版及其来源不可覆盖；撤下须单独操作。"""
    client, _ = admin
    author_id = client.post('/admin/api/authors', json={
        'canonical_name': '小传校订作者', 'dynasty': 'tang'}, headers=auth()).json()['id']
    original = client.post(f'/admin/api/authors/{author_id}/biographies', json={
        'body': '原始小传。', 'summary': '原摘要'}, headers=auth()).json()
    before = client.get(f'/admin/api/authors/{author_id}', headers=auth()).json()['biographies']
    source = next(row for row in before if row['id'] == original['id'])
    assert source['source_title'] and source['origin_type'] == 'editorial'
    assert source['status'] == 'staged'
    revision = client.post(
        f'/admin/api/authors/{author_id}/biographies/{original["id"]}/revisions',
        json={'body': '核对后的新小传。', 'summary': '新摘要'}, headers=auth())
    assert revision.status_code == 200, revision.text
    after = client.get(f'/admin/api/authors/{author_id}', headers=auth()).json()['biographies']
    old = next(row for row in after if row['id'] == original['id'])
    new = next(row for row in after if row['id'] == revision.json()['id'])
    assert old['body'] == '原始小传。' and old['status'] == 'staged'
    assert new['body'] == '核对后的新小传。' and new['status'] == 'staged'
    assert new['revises_biography_id'] == original['id']
    assert new['source_title'] == '诗卷人工编辑'
    wrong = client.post(
        f'/admin/api/authors/{author_id + 1}/biographies/{original["id"]}/revisions',
        json={'body': '不能跨作者修订'}, headers=auth())
    assert wrong.status_code in (404, 422)
    assert client.delete(f'/admin/api/materials/{original["material_id"]}', headers=auth()).status_code == 200
    latest = client.get(f'/admin/api/authors/{author_id}', headers=auth()).json()['biographies']
    assert next(row for row in latest if row['id'] == original['id'])['status'] == 'withdrawn'
    assert next(row for row in latest if row['id'] == revision.json()['id'])['status'] == 'staged'


def test_imported_biography_shows_source_file_and_keeps_import_immutable(admin):
    """导入小传可在后台查看源文件定位；修订不更改原仓库记录。"""
    from poets_catalog.importer import load
    from pathlib import Path
    from poets_catalog.models import AuthorAttribution
    client, conn = admin
    root = Path(__file__).parent / 'fixtures' / 'v1'
    load(root, ['tang'], 'biography-source-test')
    author_id = conn.scalar(select(AuthorAttribution.author_id).where(
        AuthorAttribution.source_path == '全唐诗/authors.tang.json',
        AuthorAttribution.original_name == '乙').order_by(AuthorAttribution.id.desc()).limit(1))
    detail = client.get(f'/admin/api/authors/{author_id}', headers=auth()).json()
    imported = next(b for b in detail['biographies'] if b['origin_type'] == 'imported')
    assert imported['source_title'] == 'chinese-poetry'
    assert imported['source_path'] == '全唐诗/authors.tang.json'
    assert imported['source_index'] == 2
    assert '/blob/' in imported['source_file_url']
    created = client.post(f'/admin/api/authors/{author_id}/biographies/{imported["id"]}/revisions',
                          json={'body':'自行考证后另写的小传。'}, headers=auth())
    assert created.status_code == 200
    later = client.get(f'/admin/api/authors/{author_id}', headers=auth()).json()['biographies']
    assert next(b for b in later if b['id'] == imported['id'])['body'] == imported['body']
    assert next(b for b in later if b['id'] == created.json()['id'])['revises_biography_id'] == imported['id']


def test_preview_prefers_approved_new_biography_over_staged_import(admin):
    """作者有待审旧版和已发布新版时，预览作品应展示已发布小传。"""
    from uuid import UUID
    from poets_catalog.catalog import CatalogRepository
    client, conn = admin
    author_id = client.post('/admin/api/authors', json={
        'canonical_name': '多版小传作者', 'dynasty': 'tang'}, headers=auth()).json()['id']
    old = client.post(f'/admin/api/authors/{author_id}/biographies', json={
        'body': '旧版待审核小传。'}, headers=auth()).json()
    new = client.post(f'/admin/api/authors/{author_id}/biographies/{old["id"]}/revisions',
                      json={'body': '已核权的新版小传。'}, headers=auth()).json()
    approval = {'decision': 'approved', 'legal_basis': '测试原创',
                'permitted_scope': 'web', 'evidence_uri': 'test://permission'}
    assert client.post(f'/admin/api/materials/{new["material_id"]}/reviews',
                       json=approval, headers=auth()).status_code == 200
    work = client.post('/admin/api/works', json={
        'genre': 'tang_poem', 'author_name': '多版小传作者', 'author_id': author_id,
        'title': '测试作品', 'paragraphs': ['正文。']}, headers=auth()).json()
    detail = CatalogRepository(conn, 'preview').detail(UUID(work['public_id']))
    assert detail['author_bio'] == '已核权的新版小传。'
    # 一旦撤下新版，预览可回退到仍在库的旧版；公开模式不应回退到未审旧版。
    assert client.delete(f'/admin/api/materials/{new["material_id"]}', headers=auth()).status_code == 200
    detail = CatalogRepository(conn, 'preview').detail(UUID(work['public_id']))
    assert detail['author_bio'] == '旧版待审核小传。'


def test_work_editorial_sections_and_revisions(admin):
    """已有赏析、译文、拼音和年代可读取并基于原版追加修订。"""
    client, _ = admin
    work = client.post('/admin/api/works', json={'genre':'tang_poem','author_name':'测试作者',
        'title':'测试题', 'paragraphs':['明月。']}, headers=auth()).json()
    ident = work['id']
    commentary = client.post(f'/admin/api/works/{ident}/commentaries',
        json={'title':'初稿','body':'初稿赏析。'},headers=auth()).json()
    translation = client.post(f'/admin/api/works/{ident}/translations',
        json={'language_tag':'en','blocks':['Moon.']},headers=auth()).json()
    pinyin = client.post(f'/admin/api/works/{ident}/pinyin',json={'tokens':[
        {'paragraph_index':0,'char_index':0,'character':'明','pinyin':'míng'}]},headers=auth()).json()
    date = client.post(f'/admin/api/works/{ident}/dates',json={'year_start':700,'year_end':702,
        'date_precision':'range','confidence':'low'},headers=auth()).json()
    listing = client.get(f'/admin/api/works/{ident}/editorial',headers=auth())
    assert listing.status_code == 200
    data = listing.json()
    assert data['commentaries'][0]['body'] == '初稿赏析。'
    assert data['translations'][0]['blocks'] == ['Moon.']
    assert data['pinyin'][0]['tokens'][0]['pinyin'] == 'míng'
    assert data['dates'][0]['id'] == date['id']
    new_comment = client.post(f'/admin/api/works/{ident}/commentaries/{commentary["id"]}/revisions',
        json={'title':'新稿','body':'新稿赏析。'},headers=auth())
    new_translation = client.post(f'/admin/api/works/{ident}/translations/{translation["id"]}/revisions',
        json={'language_tag':'en','blocks':['Bright moon.']},headers=auth())
    new_pinyin = client.post(f'/admin/api/works/{ident}/pinyin/{pinyin["id"]}/revisions',
        json={'tokens':[{'paragraph_index':0,'char_index':0,'character':'明','pinyin':'míng2'}]},headers=auth())
    assert new_comment.status_code == new_translation.status_code == new_pinyin.status_code == 200
    after = client.get(f'/admin/api/works/{ident}/editorial',headers=auth()).json()
    for key, old, new in [('commentaries',commentary,new_comment),('translations',translation,new_translation),('pinyin',pinyin,new_pinyin)]:
        assert len(after[key]) == 2
        assert after[key][0]['revises_id'] == old['id']
        assert after[key][0]['status'] == 'staged'
        assert after[key][1]['id'] == old['id']
        assert after[key][1]['status'] == 'staged'
    assert after['commentaries'][1]['body'] == '初稿赏析。'
    assert after['translations'][1]['blocks'] == ['Moon.']
    assert client.post(f'/admin/api/works/{ident+1}/commentaries/{commentary["id"]}/revisions',
        json={'body':'跨作品'},headers=auth()).status_code in (404,422)
