# 诗卷 · 古典诗词目录

从相邻的 [`chinese-poetry`](../chinese-poetry) 仓库导入唐诗、宋诗和宋词，存入 PostgreSQL；用 FastAPI 输出只读 JSON API 和**服务端渲染（SSR）**的检索、作品阅读页。原始 JSON 保持只读，现有的 `poetry` 数据库不受影响。

## 目录结构

```text
poets/
├── src/poets_catalog/
│   ├── api.py          # HTTP 入口、预览访问限制与 JSON API
│   ├── catalog.py      # API 与 HTML 共用的检索/详情、版权可见性查询
│   ├── site.py         # HTML 路由、页面元信息、robots 与 sitemap
│   ├── importer.py     # 可重复运行的数据导入及索引补齐 CLI
│   ├── models.py       # SQLAlchemy 数据模型
│   ├── db.py           # 数据库连接
│   └── web/
│       ├── templates/  # Jinja2：基础页、检索列表、独立作品页
│       └── static/css/ # 按基础、首页、列表、阅读、响应式分层的 CSS
├── migrations/         # Alembic 历史迁移，必须提交到 Git
├── tests/              # API、SSR、导入及授权门禁测试与样本数据
├── archive/react-spa-v1/ # 旧 React/Vite 页面备份，不再运行
├── pyproject.toml      # Python 依赖与 poets-import 命令
├── uv.lock             # 锁定 Python 依赖
└── SCHEMA_AUDIT.md     # 逐表数据及约束核查
```

CSS 不改用 Tailwind：这个阅读站已有完整的自定义视觉设计，将旧的单行大样式格式化并分成可读文件，比把大量样式类塞进模板更易让人和 AI 共同维护。公共色值与排版原则在 `foundation.css`，组件样式留在对应文件中；调整样式无需重新构建或启动 Node 服务。

## 安装和导入

需要 Python 3.11+、`uv`、PostgreSQL 16；默认连接本机 Unix socket 的 `poets_dev`，读取同级目录中的 `../chinese-poetry`。数据库**已存在时不要重复运行** `createdb`。

```sh
cd /Users/atomy/orca/projects/poets/poets
createdb poets_dev                   # 仅首次创建时执行
uv sync --extra test
uv run alembic upgrade head
uv run poets-import scan             # 只读检查与数量统计
uv run poets-import load             # 作者、作品、版本与分段正文
uv run poets-import reindex          # 为早期版本补齐检索索引，可重复运行
uv run poets-import verify           # 源数量、入库数量、检索索引核对
```

预期源作品约 332,908 首。`load` 可重跑：未变化的作品跳过，原文修订创建新版本；单个分片失败会回滚并报告。可用 `--datasets tang song ci`（或子集）、`--source-dir /path/to/chinese-poetry`、`--dry-run`、`--report report.json` 控制导入。脚本不会猜测作品年代、生成译文或自动批准公开授权。

自定义数据库需为迁移、导入与站点**同时**设置 `DATABASE_URL=postgresql+psycopg://user:password@host:5432/database`，不要把密码提交到仓库。

## 本机运行 SSR 页面

```sh
uv run uvicorn poets_catalog.api:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>。首页 `/`、搜索 `/search?q=苏轼&field=author&genre=song_ci`、作品 `/works/{public_id}` 均返回已经包含内容的 HTML；不需要 JavaScript、Node 或前端构建。表单使用 GET，筛选、分页和作品链接均可复制。JSON 接口仍为 `/api/meta`、`/api/works` 和 `/api/works/{public_id}`。静态样式由 `/static/css/site.css` 加载。

**注意版权门禁**：默认 `CATALOG_VIEW=preview`，仅本机回环地址可访问未审核内容，所有预览 HTML 均标记 `noindex,nofollow`，robots 禁止抓取，且不提供 sitemap。**不要通过公网反向代理暴露预览模式或原始数据库。** 正式公开前逐条完成权利审查，设置 `CATALOG_VIEW=public` 和 `SITE_URL=https://实际域名`，再部署只读站点。公开模式只渲染 `public_work_versions` 和有独立批准的作者简介，目前未审核时正常返回空馆藏。配置可信的 HTTPS `SITE_URL` 后，公开首页与获准作品页提供独立标题、描述、canonical 和 CreativeWork 结构化数据；检索页 `noindex`，`robots.txt` 指向仅包含获准作品的分页 sitemap。撤销授权后页面立即变为 404，sitemap 同步移除，响应为 `no-store`。

上游仓库采用 MIT 不代表其互联网来源的所有现代整理文字、作者简介及将来译文均已取得转载授权。详情页只轻量展示来源项目入口；数据库仍保存原 Git 提交和文件路径用于内部追溯。

## 验证

```sh
createdb poets_test  # 仅首次创建测试数据库时执行
DATABASE_URL=postgresql+psycopg:///poets_test uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg:///poets_test uv run pytest -q
uv run alembic check
```

测试覆盖导入幂等、繁简检索、作者身份、逐段对齐、只读 API、SSR 原文、公开权利门禁、robots 和 sitemap。数据库的 16 张业务表及当前数据核查详见 [`SCHEMA_AUDIT.md`](SCHEMA_AUDIT.md)。
