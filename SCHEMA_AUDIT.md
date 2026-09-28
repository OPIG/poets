# Database audit — 2026-09-28

Scope: all 19 catalog tables and the two guarded views in `poets_dev`, compared against the importer and the source JSON. This is a structural/data-quality audit, **not** a copyright clearance.

| Table | Finding |
| --- | --- |
| `sources` | One Git-commit snapshot records the imported repository. Repository MIT is explicitly noted as insufficient evidence for upstream rights. No file-by-file third-party provenance exists in the source dataset. |
| `authors` | Internal bigint PK plus persisted opaque UUID `public_id`; names and file positions are not identity keys. All 14,122 identities remain unverified historically. |
| `author_attributions` | 14,172 source records retain original names, IDs, biographies and file positions; 50 ambiguous CI homonyms are intentionally not linked to persons. |
| `works` | 332,908 rows, unique opaque public IDs. `source_lookup_key` is **only** an importer locator; it must not be exposed as a public/historical identity. 1,938 works have no confidently matched author profile; their original author name remains available. |
| `work_versions` | Every work has exactly one current version; original payload, source locator and hash agree with its material. Versioned source text is immutable except for switching `is_current`. The original raw JSON contributes substantially to DB size; retaining it is intentional for provenance. |
| `work_paragraphs` | 1,526,487 ordered paragraphs match the original JSON arrays. 27 works have empty `paragraphs` in the source; these are imported with warnings, not invented text. |
| `work_dates` | Empty as expected: the source has no reliable creation dates. A work may have at most one preferred claim, with year order checked. Evidence and editorial review are still required before timeline display. |
| `author_events` | Empty as expected. Year order checked; source citations are required. No factual timeline is inferred from unstructured biography. |
| `materials` | 345,922 imported texts are staged, not published; discriminator and hash are immutable. A trigger verifies the material kind for each typed content table. |
| `author_biographies` | 13,014 raw imported biographies are staged; not all author records contain a biography. More than one edition per author is possible; a future editorial UI must explicitly choose what to feature. |
| `translation_editions` | Empty. Each translation is linked to a work version and a matching-language translation material. One work may have several languages/translators. |
| `translation_blocks` | Empty. Alignment trigger rejects paragraph references from another version and enforces whole-work vs paragraph modes; ordering is unique per edition. |
| `commentaries` | Empty. Material type and optional paragraph-version alignment are checked. Critic attribution and source rights still require human review. |
| `pinyin_sets` | Empty. Each set is bound to the exact text hash; JSON token positions still require application-level validation against the paragraph text before publication. |
| `work_searches` | Normalized author, title/词牌 and tags for all 332,908 versions; the index can be rebuilt with `poets-import reindex`. This lookup table is not a public API and contains no copyright approval. |
| `admin_users` | 管理员账号、Argon2id 密码摘要与失败锁定状态；不可通过公共 API 读取。 |
| `admin_sessions` | 8 小时有效的数据库会话，仅保存 Cookie 摘要；退出或修改密码使其失效，CSRF 随会话恢复。 |
| `admin_audit_logs` | 后台新增的操作追溯表，仅追加管理动作摘要；不保存令牌和权利证据全文。 |
| `rights_reviews` | Empty. Approval requires legal basis, permitted scope and evidence URI; a current review is unique per material. It is not a substitute for legal judgment. |

`public_materials` filters published, current, unexpired and hash-matching approvals; `public_work_versions` further requires the current original material and exposes an opaque work UUID. Both currently return zero rows. A future public app should use a restricted read-only DB role with access to guarded views, **not** grant it access to raw tables. Biography/translation/commentary views must additionally check their referenced work version's publication policy.

## Follow-up decisions (not inferred from source data)

- Work identity for CI still uses file+row as an **import lookup**, because the source has no stable ID. The importer quarantines suspicious changes, but a future source-file reorder needs a reviewed reconciliation process; do not silently rebind old translations or notes.
- A UUID is stable in this database. A full database rebuild from JSON alone cannot preserve the same randomly issued UUIDs; export the identity mapping or restore the database if permanent external URLs are required.
- Chinese short-keyword search, author alias management, and character-offset validation for pinyin should be implemented and measured with the future reading/API layer, not inferred from the current JSON.
