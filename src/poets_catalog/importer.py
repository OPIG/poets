"""从相邻 chinese-poetry 仓库执行可重跑的 ETL（抽取、转换、入库）。

只导入原始诗词和作者资料，不推断创作年代、作者同名身份或转载授权。
入库内容默认私有暂存；数据库中的公开视图另行检查内容审核与权利证据。
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import json
import logging
from pathlib import Path
from uuid import uuid4
import subprocess
import unicodedata

from opencc import OpenCC
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from .db import engine
from .models import (
    Author, AuthorAttribution, AuthorBiography, Material, Source, Work,
    WorkParagraph, WorkVersion,
)

LOG = logging.getLogger(__name__)
# 归一化仅用于检索与谨慎匹配；正文和作者原始写法永不被覆盖。
CONVERTER = OpenCC("t2s")
# (作品分片 glob, 作者资料文件, 作品类型, 朝代)。刻意不导入选集，避免重复。
SPECS = {
    "tang": ("全唐诗/poet.tang.*.json", "全唐诗/authors.tang.json", "tang_poem", "tang"),
    "song": ("全唐诗/poet.song.*.json", "全唐诗/authors.song.json", "song_poem", "song"),
    "ci": ("宋词/ci.song.*.json", "宋词/author.song.json", "song_ci", "song"),
}


def digest(data) -> str:
    """计算确定性的 JSON 摘要；字段顺序变化不应生成新原文版本。"""
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def normalize(value: str) -> str:
    """统一 Unicode、繁简与大小写，供检索和候选身份核对使用。"""
    return CONVERTER.convert(unicodedata.normalize("NFKC", value)).casefold()


def source_revision(root: Path, supplied: str | None) -> str:
    """取得源仓库提交号；有未提交修改时拒绝冒用原提交号。"""
    if supplied:
        return supplied
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                            capture_output=True, text=True, check=True)
    if status.stdout.strip():
        raise ValueError("Source checkout has uncommitted changes; commit them or pass an explicit --revision label")
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def selected_files(root: Path, datasets: list[str]):
    """依配置枚举作者文件及作品分片，只读取指定数据集。"""
    for name in datasets:
        pattern, author_path, genre, dynasty = SPECS[name]
        author = root / author_path
        if not author.is_file():
            raise FileNotFoundError(author)
        yield name, author, sorted(root.glob(pattern)), genre, dynasty


def read_rows(path: Path):
    """读取单个 JSON 数组；每次只加载一份文件而非整库。"""
    with path.open(encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError(f"Expected a JSON array: {path}")
    return rows


def report_scan(root: Path, datasets: list[str]) -> dict:
    """不连接数据库，验证文件存在、可解析并统计作者与作品数量。"""
    result = {}
    for name, author, files, _, _ in selected_files(root, datasets):
        if not files:
            raise FileNotFoundError(f"No files for {name}")
        result[name] = {"author_records": len(read_rows(author)), "work_files": len(files),
                        "work_records": sum(len(read_rows(p)) for p in files)}
    return result


def ensure_source(conn, revision: str) -> int:
    """按 Git 提交号幂等登记来源快照，返回 sources.id。"""
    key = f"chinese-poetry:{revision}"
    conn.execute(insert(Source).values(key=key, kind="repository_snapshot", title="chinese-poetry",
        url="https://github.com/chinese-poetry/chinese-poetry", commit_sha=revision,
        license_note="Repository MIT; upstream data rights not established; no automatic publication.",
        checked_at=datetime.now(timezone.utc)).on_conflict_do_nothing(index_elements=[Source.key]))
    return conn.scalar(select(Source.id).where(Source.key == key))


def chunks(values, size=500):
    """分批生成 SQL 入参，避免单次插入超过 PostgreSQL 参数上限。"""
    for start in range(0, len(values), size):
        yield values[start:start + size]


def insert_authors(conn, root: Path, path: Path, dataset: str, dynasty: str, source_id: int):
    """导入作者出处及待审核简介，返回本资料文件中唯一姓名到人物 ID 的映射。"""
    rows = read_rows(path)
    relative = path.relative_to(root).as_posix()
    counts = Counter(row.get("name") for row in rows if isinstance(row, dict))
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError(f"Invalid author at {relative}:{i}")

    # Source records determine repeatability. Person IDs are opaque and never
    # derived from names, their script variants, or row positions.
    history = conn.execute(select(AuthorAttribution.source_id, AuthorAttribution.source_index,
        AuthorAttribution.original_id, AuthorAttribution.original_name,
        AuthorAttribution.raw_biography, AuthorAttribution.author_id)
        .where(AuthorAttribution.source_path == relative)).all()
    # 同一提交的同一数组位置可直接复用；不同提交要寻找更强的身份证据。
    exact = {index: aid for sid,index,_,_,_,aid in history if sid == source_id}
    by_original_id = defaultdict(set)
    by_locator = defaultdict(list)
    for sid,index,original_id,name,bio,aid in history:
        if original_id and aid is not None:
            by_original_id[original_id].add(aid)
        by_locator[index].append((name, bio, aid))

    resolved = {}
    statuses = {}
    fresh = []
    for i, row in enumerate(rows):
        original_id = str(row["id"]) if row.get("id") is not None else None
        # 无原始 ID 且同名：不能仅凭名字或繁简归一化合并历史人物。
        if counts[row["name"]] > 1 and not original_id:
            resolved[i], statuses[i] = exact.get(i), "ambiguous"
            continue
        if i in exact:
            resolved[i], statuses[i] = exact[i], "source_record" if exact[i] else "pending_identity"
            continue
        # 唐宋诗作者有源 ID，优先跨提交沿用该来源记录的已核验人物。
        if original_id:
            candidates = by_original_id[original_id]
            if len(candidates) == 1:
                resolved[i], statuses[i] = next(iter(candidates)), "source_id"
            elif candidates:
                resolved[i], statuses[i] = None, "ambiguous"
            else:
                fresh.append(i)
            continue
        # 宋词作者无源 ID：须同时满足位置、归一化姓名和简介高度相似。
        # 位置被重排或证据不够时宁可待核，不把旧传记接到新人身上。
        candidates = set()
        for previous_name, previous_bio, aid in by_locator[i]:
            if aid is None or normalize(previous_name) != normalize(row["name"]):
                continue
            current_bio = row.get("desc") or row.get("description") or ""
            old_bio = previous_bio or ""
            if SequenceMatcher(None, normalize(old_bio), normalize(current_bio)).ratio() >= 0.9:
                candidates.add(aid)
        if len(candidates) == 1:
            resolved[i], statuses[i] = next(iter(candidates)), "verified_locator"
        elif by_locator[i]:
            resolved[i], statuses[i] = None, "pending_identity"
        else:
            fresh.append(i)

    # 人物对外 ID 是持久化的随机 UUID，不编码姓名、脚本或源文件位置。
    new_ids = {i: uuid4() for i in fresh}
    for batch in chunks(fresh):
        conn.execute(insert(Author).values([dict(public_id=new_ids[i], canonical_name=rows[i]["name"],
            dynasty=dynasty) for i in batch]))
    if new_ids:
        inserted = dict(conn.execute(select(Author.public_id, Author.id).where(
            Author.public_id.in_(new_ids.values()))).all())
        for i, identifier in new_ids.items():
            resolved[i], statuses[i] = inserted[identifier], "new"

    attrs = [dict(author_id=resolved.get(i), source_id=source_id, source_path=relative, source_index=i,
        original_id=str(row["id"]) if row.get("id") is not None else None,
        original_name=row["name"], raw_biography=row.get("desc") or row.get("description"),
        raw_short_biography=row.get("short_description"), match_status=statuses[i])
        for i,row in enumerate(rows)]
    for batch in chunks(attrs):
        conn.execute(insert(AuthorAttribution).values(batch).on_conflict_do_nothing(
            index_elements=[AuthorAttribution.source_id, AuthorAttribution.source_path, AuthorAttribution.source_index]))
    # 原仓库中的现代简介仍可能有上游权利问题，因此仅作为 staged 材料。
    bio_rows = [(resolved[i], body, f"bio:{resolved[i]}:{digest(body)}") for i,row in enumerate(rows)
                if resolved.get(i) is not None and (body := row.get("desc") or row.get("description"))]
    if bio_rows:
        for batch in chunks(bio_rows):
            conn.execute(insert(Material).values([dict(external_key=key, kind="biography", source_id=source_id,
                language_tag="zh", origin_type="imported", workflow_status="staged", content_hash=digest(body))
                for _,body,key in batch]).on_conflict_do_nothing(index_elements=[Material.external_key]))
        material_ids = dict(conn.execute(select(Material.external_key, Material.id).where(
            Material.external_key.in_([b[2] for b in bio_rows]))).all())
        for batch in chunks(bio_rows):
            conn.execute(insert(AuthorBiography).values([dict(author_id=aid, material_id=material_ids[key], body=body)
                for aid,body,key in batch]).on_conflict_do_nothing(index_elements=[AuthorBiography.material_id]))
    return {row["name"]: resolved[i] for i,row in enumerate(rows)
            if counts[row["name"]] == 1 and resolved.get(i) is not None}


def prepare_work(row, relative, index, genre, authors):
    """验证一条作品并计算导入定位键、正文、摘要和检索文本。"""
    if not isinstance(row, dict) or not isinstance(row.get("author"), str):
        raise ValueError("missing author")
    paragraphs = row.get("paragraphs")
    if not isinstance(paragraphs, list) or not all(isinstance(v, str) for v in paragraphs):
        raise ValueError("paragraphs must be an array of strings")
    if genre == "song_ci":
        # 宋词缺少源 ID：文件位置只是内部导入查找键，公开身份是 works.public_id。
        identity = f"song_ci:{relative}:{index}"
    else:
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("missing source id")
        identity = f"{genre}:{row['id']}"
    # 作品摘要覆盖标题、作者、正文及原始附加字段；任一变化会触发新版本。
    body = "\n".join(paragraphs)
    return dict(identity=identity, row=row, index=index, author_id=authors.get(row["author"]),
        hash=digest(row), body=body, search=normalize(" ".join(
            [row.get("title") or "", row.get("rhythmic") or "", row["author"], body])))


def suspicious_ci_change(old_payload, new_payload):
    """判断无原始 ID 的宋词是否可能被同位置的另一首词替换。"""
    old_lines = old_payload.get("paragraphs") or []
    new_lines = new_payload.get("paragraphs") or []
    if old_payload.get("author") != new_payload.get("author") or old_payload.get("rhythmic") != new_payload.get("rhythmic"):
        return True
    if not old_lines or not new_lines:
        return True
    return SequenceMatcher(None, old_lines[0], new_lines[0]).ratio() < 0.8


def import_file(conn, root, path, genre, authors, source_id, issues, warnings):
    """在调用方事务中导入一个作品文件；返回有效条数和新版本条数。"""
    relative = path.relative_to(root).as_posix()
    rows = read_rows(path)
    prepared = []
    for i,row in enumerate(rows):
        try:
            item = prepare_work(row, relative, i, genre, authors)
            if not item["row"]["paragraphs"]:
                warnings.append(f"{relative}:{i}: empty paragraphs in source")
            prepared.append(item)
        except (ValueError, TypeError) as exc:
            issues.append(f"{relative}:{i}: {exc}")
    if not prepared:
        return 0, 0
    # 先确保作品实体存在；冲突时不覆盖已人工校订的人物关联或公开 UUID。
    conn.execute(insert(Work).values([dict(source_lookup_key=p["identity"], genre=genre,
        author_id=p["author_id"], original_author_name=p["row"]["author"]) for p in prepared
        ]).on_conflict_do_nothing(index_elements=[Work.source_lookup_key]))
    keys = [p["identity"] for p in prepared]
    work_ids = dict(conn.execute(select(Work.source_lookup_key, Work.id).where(Work.source_lookup_key.in_(keys))).all())
    # 一次查出当前版本，避免对每首诗单独查询数据库。
    current = {wid: (vid, h, payload) for wid,vid,h,payload in conn.execute(
        select(WorkVersion.work_id, WorkVersion.id, WorkVersion.content_hash, WorkVersion.raw_payload)
        .where(WorkVersion.work_id.in_(work_ids.values()), WorkVersion.is_current.is_(True)))}
    changed = []
    for p in prepared:
        wid = work_ids[p["identity"]]
        previous = current.get(wid)
        if previous and previous[1] == p["hash"]:
            continue
        # 宋词换位有误连风险：先报告，不替换旧版及其译文、拼音关联。
        if previous and genre == "song_ci" and suspicious_ci_change(previous[2], p["row"]):
            issues.append(f"{relative}:{p['index']}: possible identity shift; not updated")
            continue
        p["work_id"] = wid
        changed.append(p)
    if not changed:
        return len(prepared), 0
    existing_numbers = dict(conn.execute(select(WorkVersion.work_id, func.max(WorkVersion.version_number))
        .where(WorkVersion.work_id.in_([p["work_id"] for p in changed])).group_by(WorkVersion.work_id)).all())
    for p in changed:
        p["version_number"] = existing_numbers.get(p["work_id"], 0) + 1
        p["material_key"] = f"work:{p['identity']}:{p['version_number']}"
    conn.execute(insert(Material).values([dict(external_key=p["material_key"], kind="original",
        source_id=source_id, language_tag="zh", origin_type="imported", workflow_status="staged",
        content_hash=p["hash"]) for p in changed]))
    materials = dict(conn.execute(select(Material.external_key, Material.id).where(
        Material.external_key.in_([p["material_key"] for p in changed]))).all())
    # 旧版原文不改写；在同一文件事务内撤销 current 并插入新版本。
    previous_ids = [current[p["work_id"]][0] for p in changed if p["work_id"] in current]
    if previous_ids:
        conn.execute(update(WorkVersion).where(WorkVersion.id.in_(previous_ids)).values(is_current=False))
    version_rows = [dict(work_id=p["work_id"], material_id=materials[p["material_key"]],
        source_id=source_id, source_path=relative, source_index=p["index"],
        original_id=str(p["row"]["id"]) if p["row"].get("id") is not None else None,
        title=p["row"].get("title"), rhythmic=p["row"].get("rhythmic"),
        prologue=p["row"].get("prologue"), tags=p["row"].get("tags") or [],
        raw_payload=p["row"], content_text=p["body"], search_text=p["search"],
        content_hash=p["hash"], version_number=p["version_number"], is_current=True) for p in changed]
    returned = conn.execute(insert(WorkVersion).values(version_rows).returning(
        WorkVersion.work_id, WorkVersion.id))
    version_ids = dict(returned.all())
    paragraphs = [dict(work_version_id=version_ids[p["work_id"]], paragraph_index=i, body=body)
        for p in changed for i,body in enumerate(p["row"]["paragraphs"])]
    if paragraphs:
        # 分段写入，避免少数长篇作品让单条 SQL 的参数数量过大。
        for start in range(0, len(paragraphs), 1000):
            conn.execute(insert(WorkParagraph).values(paragraphs[start:start+1000]))
    return len(prepared), len(changed)


def load(root: Path, datasets: list[str], revision: str, dry_run=False):
    """先导作者再逐文件提交作品；单文件失败回滚并继续记录问题。"""
    if dry_run:
        return report_scan(root, datasets)
    db = engine()
    totals = Counter()
    issues = []
    warnings = []
    try:
        with db.begin() as conn:
            source_id = ensure_source(conn, revision)
        for name, author_file, files, genre, dynasty in selected_files(root, datasets):
            if not files:
                raise FileNotFoundError(f"No data files for {name}")
            # 作者表和来源署名在一个事务中完成；作品按文件独立提交，便于续跑。
            with db.begin() as conn:
                authors = insert_authors(conn, root, author_file, name, dynasty, source_id)
            LOG.info("%s: %d unambiguous author names", name, len(authors))
            for path in files:
                file_issues = []
                file_warnings = []
                try:
                    with db.begin() as conn:
                        scanned, changed = import_file(conn, root, path, genre, authors, source_id, file_issues, file_warnings)
                # 文件事务失败后整份文件回滚，其余分片仍可继续；最终退出码非零。
                except (SQLAlchemyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    issues.append(f"{path.relative_to(root)}: transaction rolled back: {exc}")
                    continue
                issues.extend(file_issues)
                warnings.extend(file_warnings)
                totals[f"{name}_scanned"] += scanned
                totals[f"{name}_changed"] += changed
                LOG.info("%s: scanned=%d changed=%d issues=%d", path.name, scanned, changed, len(file_issues))
        return {"totals": dict(totals), "issues": issues, "warnings": warnings}
    finally:
        db.dispose()


def verify(root: Path, datasets: list[str]):
    """比较源数组数量与已入库作品，并报告暂存与权利审核数量。"""
    expected = report_scan(root, datasets)
    db = engine()
    try:
        with db.connect() as conn:
            actual = dict(conn.execute(select(Work.genre, func.count()).group_by(Work.genre)).all())
            versions = conn.scalar(select(func.count()).select_from(WorkVersion))
            private = conn.scalar(select(func.count()).select_from(Material).where(Material.workflow_status == "staged"))
            rights = conn.scalar(text("SELECT count(*) FROM rights_reviews WHERE decision = 'approved'"))
        comparison = {name: {"expected": expected[name]["work_records"], "actual": actual.get(SPECS[name][2], 0)}
            for name in datasets}
        return {"datasets": comparison, "versions": versions, "staged_materials": private,
                "approved_rights_reviews": rights, "matches": all(v["expected"] == v["actual"] for v in comparison.values())}
    finally:
        db.dispose()


def main():
    """解析命令行，输出机器可读 JSON；问题或核对失败时返回非零退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["scan", "load", "verify"])
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parents[3] / "chinese-poetry")
    parser.add_argument("--datasets", nargs="+", choices=list(SPECS), default=list(SPECS))
    parser.add_argument("--revision", help="Override Git commit for a non-Git fixture checkout")
    parser.add_argument("--dry-run", action="store_true", help="Validate input without writing to the database")
    parser.add_argument("--report", type=Path, help="Optional JSON output report")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = args.source_dir.resolve()
    if not root.is_dir():
        parser.error(f"Source directory not found: {root}")
    if args.command == "scan" or args.dry_run:
        result = report_scan(root, args.datasets)
    elif args.command == "load":
        result = load(root, args.datasets, source_revision(root, args.revision))
    else:
        result = verify(root, args.datasets)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if args.report:
        args.report.write_text(serialized + "\n", encoding="utf-8")
    if args.command == "verify" and not result["matches"]:
        parser.exit(1)
    if args.command == "load" and result["issues"]:
        parser.exit(1)


if __name__ == "__main__":
    main()
