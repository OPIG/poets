# 诗卷 · 古典诗词目录

从相邻的 [`chinese-poetry`](../chinese-poetry) 仓库导入唐诗、宋诗和宋词，存入 PostgreSQL；用 FastAPI 输出只读 JSON API 和**服务端渲染（SSR）**的检索、作品阅读页。原始 JSON 保持只读，现有的 `poetry` 数据库不受影响。

## 目录结构

```text
poets/
├── src/poets_catalog/
│   ├── admin/          # 独立的后台 API、事务服务、表单与权限校验
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

测试覆盖导入幂等、繁简检索、作者身份、逐段对齐、只读 API、SSR 原文、公开权利门禁、robots 和 sitemap。数据库的 19 张业务表及当前数据核查详见 [`SCHEMA_AUDIT.md`](SCHEMA_AUDIT.md)。

## 后台内容管理

后台逻辑**独立**位于 `src/poets_catalog/admin/`：

- `auth.py`：账号密码、Argon2id 哈希、8 小时会话、CSRF 校验、失败锁定与首次创建账号 CLI。
- `routes.py`：受会话保护的 `/admin/api/` 接口和 `/admin` 登录页面。
- `schemas.py`：字段白名单及表单校验，不接受任意 SQL。
- `service.py`：事务式增改、软撤下、版本管理、审核与审计。
- `templates/`、`static/`：后台页面；无持久化密码或浏览器本地令牌。

先运行 `uv run alembic upgrade head`，再通过交互式命令**仅创建一次**管理员账号；密码会在终端隐藏输入，不会出现在 shell 历史或仓库中：

```sh
uv run poets-admin create admin
uv run uvicorn poets_catalog.api:app --host 127.0.0.1 --port 8000
```

开发时修改 Python 后台路由后，须重启未启用热重载的 Uvicorn 进程；只刷新浏览器只能更新页面和静态资源，不能使运行中的旧路由生效。可在本机开发时给 Uvicorn 加 `--reload`，正式运行不建议使用。

打开 `http://127.0.0.1:8000/admin`，使用账号密码登录。登录 Cookie 为 HttpOnly、SameSite=Strict；8 小时后自动过期。刷新页面时会在同一会话内恢复，退出立即撤销。连续 5 次输错密码将锁定 15 分钟。忘记密码或需要主动撤销全部旧会话时在**本机终端**执行 `uv run poets-admin reset-password admin`。

本机 `poets_dev` 已建立 `admin` 账号：初始密码保存在 Git 忽略且权限 `0600` 的 `.admin-initial-password` 文件中，可用 `cat .admin-initial-password` 在本机查看。请尽快用 `poets-admin reset-password admin` 改成自己的密码；更改后旧会话失效，初始文件不再作为登录依据。旧 `ADMIN_TOKEN` 不再参与认证。

后台默认仅接受本机回环地址请求。远程部署需要 `ADMIN_ALLOW_REMOTE=1`、HTTPS `SITE_URL`，并配合 TLS 反向代理、访问控制和专用数据库账户；远程会话 Cookie 自动设为 Secure。所有写操作校验 Origin 和 CSRF；页面标记 `noindex`、`no-store`，并设置 CSP。**不要将本机预览端口直接暴露到公网。**

登录页使用与阅读站一致的墨绿、暖金、远山与留白；山水意境由 CSS 绘制，不依赖外部图片。桌面为双栏欢迎文案与登录表单，移动端纵向排列；仅修改视觉，不改变管理员会话和 CSRF 逻辑。

后台界面交互：点击“查看 / 编辑”或“新建”会打开居中的模态弹窗；从作品或作者弹窗进入“新增赏析／译文／拼音／年代／小传／事件”等子编辑时，前端暂存父表单的真实 DOM，子表单取消、点关闭或按 `Esc` 都返回上一层，并保留未保存输入。子表单保存成功后同样返回上一层；在最外层取消才关闭弹窗。长表单只在弹窗内滚动。后台工作区固定占满视口（扣除顶部导航）；标题与筛选区留在上方，记录列表占满剩余高度并在列表内部独立滚动，侧栏不随列表滚动，分页固定在内容区底部。手机端侧栏横向滚动，列表仍独立纵向滚动。列表分页按当前筛选条件显示总条数、当前页与总页数，支持上一页、下一页和指定页码跳转；切换栏目或筛选条件后回到第一页。查找前会去除首尾空白并校验非空；空输入时检索框变红，并在 placeholder 中显示红色“请输入检索关键词”，不重新加载全库。重新聚焦或开始输入时恢复原提示和正常边框。已有筛选时使用单独的“清除筛选”恢复列表。后端列表仍返回记录数组，并通过 `X-Total-Count` 响应头提供筛选后的总数，单页默认 30 条。

后台列表不展示数据库内部枚举：`song` 显示“宋代”，`unverified` 显示“人物身份待考证”，`song_poem` 显示“宋诗”，`normal` 显示“在库（未归档）”。“在库”不等于已经获准公开，公开仍取决于材料权利审核。作品标签是可选的主题、体裁、选本等检索标记，例如“春天”“送别”“乐府”“唐诗三百首”；多个标签以英文逗号分隔，不把诗题、作者或原文重复填进标签。

作品管理与作者资料均提供“归档状态”筛选，可选择全部、未归档、已归档，并与关键词联动；状态变化会重置到第一页，列表总数和分页同步更新。已归档记录的编辑弹窗只显示“恢复作品”或“恢复人物资料”，不再显示归档按钮；未归档记录只显示对应归档按钮。恢复人物资料后状态回到“待考证”，如需标为“已核对”须另行考证；恢复作品后仍须重新审核才能公开。

- **作品**：查询、新建、修订、归档和恢复。修订创建新的不可变原文版本及独立待审核材料；归档只撤下展示，不物理删除历史。恢复后需要重新审核才能公开。规范人物 ID 是身份关联，不在普通原文编辑表单中开放，接口也拒绝借修订作品改变关联；历史署名消歧应走独立核对流程。
- **作者／来源**：可增查改、作者可归档；归档人物资料会隐藏其前台小传，并阻止新资料关联该人物，不会删除原始署名、已有作品或历史记录。可在作者编辑中恢复非归档状态。导入来源快照禁止编辑。署名消歧通过独立来源记录明确关联作者，不按同名自动合并。“source_record”是导入时采用来源记录的内部匹配代码，不是人工考据结论；列表改展示“已关联 · 待核对／已人工核对／待考证”和“唐诗／宋诗／宋词作者资料”。原 JSON 相对路径保留在可展开的来源详情中，不作为列表主信息。管理员通过姓名搜索候选、查看资料并确认后调整署名到人物的关联外键，不修改任何作者主键；解除关联也不会删掉原署名。
- **编辑内容**：可新增作者小传、赏析、整篇译文、逐字拼音；拼音位置与现存原文字符逐一核对。内容修改应产生新的材料／版本，旧材料通过撤下保留溯源，不原地覆盖。
- **时间线**：作者生平事件及作品创作时间可增查改，只有已核验年代能成为作品首选时间。来源不足时不要猜测年份。
审核列表中的 `original` 是数据表内部类型，表示作品原文材料；页面改用“作品原文”并展示作品题名／词牌、原作者署名、来源项目和材料编号。审核决定该份原文能否进入公开视图，不会因入库自动公开。批准缺少权利依据、使用范围或证据位置时，弹窗保留并在弹窗内提示，输入内容不丢失。

- **审核**：逐份材料批准、拒绝或撤销。“审核决定”始终必选；选择“批准公开”时，标 `*` 的“权利依据”“允许使用范围”“证据位置”必须填写。“权利依据”解释为什么能用这份内容，“证据位置”指向内部授权书或可核验的许可资料；“允许使用范围”当前只支持 `web`（本站网页公开展示），不包含第三方转载、原始文件批量下载、API 再分发或商业再授权，均需另行确认。拒绝／撤销时三项不强制，审核说明、到期时间始终可选。公开视图仅接受有效 `web` 批准与匹配的内容摘要，到期或撤销即下线。操作记录优先展示“批准公开 · 作品原文 · 《诗题》 · 作者”等中文动作、操作人及本地时间；技术 ID 与 JSON 可展开追溯，`admin_audit_logs` 不存密码、会话 Cookie 或证据全文。

本后台不是对所有 PostgreSQL 表的通用裸 CRUD；原始来源、历史原文、旧审核结论和审计记录需要保持可追溯，不提供物理删除按钮。生产环境建议先通过 `CATALOG_VIEW=public` 验证公开端门禁，再开放独立的管理入口。

### 作者小传的来源与校订

在“作者资料”搜索作者并打开编辑弹窗，下方“已有小传”列出每份版本的全文、来源项目、审核状态和修订关系。原仓库导入的版本还可展开查看源文件相对路径、数组位置及固定 Git 提交的文件链接。该链接只能说明**数据从何处导入**，不自动证明现代小传的上游版权。

点击某版“基于此版修订”会预填正文，并写入一条新的 `author_biographies` 和待审核 `materials`，通过 `revises_biography_id` 关联旧版；旧版正文、来源和审核记录均不覆盖，也**不自动撤下**。如确认旧版不应继续使用，另点“撤下此版”，然后分别对新版审核。小传只应依据已核实资料重写，不能把导入原文未经核权直接批准公开。
