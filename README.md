# Poets catalog

PostgreSQL schema and repeatable importer for the adjacent [chinese-poetry](../chinese-poetry) repository. This repository does **not** modify the source JSON or the existing `poetry` database.

## Local setup

Requires Python 3.11+, `uv`, and PostgreSQL 16. Default database URL uses the current local PostgreSQL user via the Unix socket.

```sh
createdb poets_dev                       # once; only if it does not already exist
uv sync --extra test
uv run alembic upgrade head
uv run poets-import scan                  # read-only source inventory
uv run poets-import load --report import-report.json
uv run poets-import verify
uv run poets-import load                  # rerun: unchanged records are skipped
```

Set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/database` to use another database. `--source-dir` defaults to `../chinese-poetry` relative to this project. `--datasets tang song ci` (or any subset) limits the import. `--dry-run` validates source counts without writing. `--revision` permits an explicit source revision for non-Git fixtures. The report file is optional; by default the JSON summary is printed to stdout. A nonzero exit code means at least one record or file needs attention; empty source paragraphs are imported but listed as warnings. Never commit credentials or authorization evidence.

For an isolated integration test database:

```sh
createdb poets_test
DATABASE_URL=postgresql+psycopg:///poets_test uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg:///poets_test uv run pytest
```

## Data model and rights

The 15 tables are grouped into provenance (`sources`), author identity (`authors`, `author_attributions`), works and immutable text revisions (`works`, `work_versions`, `work_paragraphs`), chronology (`work_dates`, `author_events`), editorial materials (`materials`, `author_biographies`, `translation_editions`, `translation_blocks`, `commentaries`, `pinyin_sets`), and rights decisions (`rights_reviews`). Migrations are the source of truth for schema changes. See [SCHEMA_AUDIT.md](SCHEMA_AUDIT.md) for a per-table data and integrity audit.

All imported original texts and biographies have `materials.workflow_status = 'staged'`; the importer creates **no** rights approval. The upstream repository's MIT license does not independently establish rights for all internet-sourced text or modern biography. A future public API must require a current, non-expired, approved `rights_reviews` row whose `reviewed_hash` matches the material's `content_hash`, as well as `workflow_status = 'published'`. The `public_materials` view centralizes those conditions; `public_work_versions` additionally checks current original text and exposes an opaque work UUID. Future public APIs should use these views and independently gate referenced biography, translation, and commentary versions. Do not expose the raw tables or reports publicly. The database contains no inferred creation dates, translations, commentary, or pinyin until separately sourced and reviewed.

Works also have opaque persisted `public_id` UUIDs; `source_lookup_key` is only for import matching, never a public identifier. For Tang/Song poems, the namespaced source ID identifies import records. Song lyrics without source IDs use file path and row index; if author, tune, or opening line changes substantially at the same locator, the importer reports a possible identity shift instead of attaching existing editorial content to potentially different text. Review such cases manually. Only unambiguous author names within their source author file are linked automatically. Authors have an opaque, persisted UUID `public_id`, independent of names and source positions. Source IDs, paths, row indices, and original spellings belong to `author_attributions`. On a later source revision, source IDs are matched first; CI records without IDs are matched at the same source locator only when normalized names and biographies agree. Ambiguous repeated names such as `蔡` remain unlinked attributions. Cross-corpus identity merging requires verification.

## 项目文件结构

- `src/poets_catalog/models.py`：15 张业务表的 SQLAlchemy 模型、主外键、唯一约束和索引定义。改结构后须新增 Alembic 迁移，不能只改模型。
- `src/poets_catalog/importer.py`：`poets-import` 命令行入口；校验来源、谨慎匹配作者、按文件事务导入、生成原文版本和核对数量。
- `src/poets_catalog/db.py`：统一读取 `DATABASE_URL`，供迁移和导入共用。
- `migrations/env.py`、`migrations/versions/`：Alembic 执行配置及按顺序执行的历史迁移；后续迁移还包含公开视图与完整性触发器。
- `tests/test_importer.py`、`tests/fixtures/`：模拟数据以及幂等、繁简、作者消歧、版本关联和版权门禁测试。
- `pyproject.toml`、`uv.lock`：项目依赖、CLI 注册和锁定版本；`alembic.ini` 为迁移入口配置。
- `SCHEMA_AUDIT.md`：逐表核查结果、已知源数据缺口与后续风险；`.gitignore` 排除虚拟环境、缓存和默认导入报告。

## 导入操作说明

在本仓库目录执行下列命令。默认读取相邻 `../chinese-poetry`；默认连接本机 `poets_dev`，不会连接已有的 `poetry` 数据库。首次部署应先确保 PostgreSQL 已启动，并创建数据库；**数据库已存在时跳过 `createdb`**。

```sh
cd /Users/atomy/orca/projects/poets/poets
createdb poets_dev
uv sync --extra test
uv run alembic upgrade head
uv run poets-import scan
uv run poets-import load --report import-report.json
uv run poets-import verify
```

导入从 Git 提交号记录源快照，分片事务失败会报告并继续其他文件，最后以非零退出码提示问题。重跑 `load` 会跳过未变化的作品；出现原文修订则新建版本。现有源数据预期唐诗 57,607、宋诗 254,248、宋词 21,053 条。`load` 的 `warnings` 可包括源数据本来缺少正文的记录，不等于自动补全。

常用变体：

```sh
uv run poets-import load --datasets ci            # 只导入宋词
uv run poets-import load --dry-run                  # 仅读取、统计，不写数据库
uv run poets-import load --source-dir /path/to/chinese-poetry
DATABASE_URL='postgresql+psycopg://user:password@host:5432/dbname' uv run alembic upgrade head
DATABASE_URL='postgresql+psycopg://user:password@host:5432/dbname' uv run poets-import load
```

若使用自定义数据库，迁移、导入、核对三步必须设置同一个 `DATABASE_URL`。切勿把真实密码写进仓库。`--revision` 仅用于非 Git 测试数据或明确标记修改过的源数据；正常情况下应先提交源仓库修改，让导入器读取真实提交号。
