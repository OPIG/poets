"""后台白名单输入模型：拒绝任意表名和字段的通用 SQL 写入。"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class SourceInput(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    url: str | None = None
    license_note: str | None = None


class AuthorInput(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=150)
    dynasty: str = Field(min_length=1, max_length=16)
    identity_status: Literal["unverified", "verified", "archived"] = "unverified"
    birth_year_min: int | None = None
    birth_year_max: int | None = None
    death_year_min: int | None = None
    death_year_max: int | None = None

    @model_validator(mode="after")
    def ordered_years(self):
        for prefix in ("birth", "death"):
            start, end = getattr(self, f"{prefix}_year_min"), getattr(self, f"{prefix}_year_max")
            if start is not None and end is not None and start > end:
                raise ValueError("生卒年份范围的起始值不可大于结束值")
        return self


class WorkInput(BaseModel):
    genre: Literal["tang_poem", "song_poem", "song_ci"]
    author_name: str = Field(min_length=1, max_length=150)
    author_id: int | None = None
    title: str | None = Field(default=None, max_length=500)
    rhythmic: str | None = Field(default=None, max_length=200)
    paragraphs: list[str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    prologue: str | None = None

    @model_validator(mode="after")
    def validate_text(self):
        if not self.title and not self.rhythmic:
            raise ValueError("诗题或词牌至少填写一项")
        if not all(isinstance(x, str) and x.strip() for x in self.paragraphs):
            raise ValueError("正文段落不可为空")
        if len(self.paragraphs) > 500 or any(len(x) > 5000 for x in self.paragraphs):
            raise ValueError("正文超过后台单篇编辑限制")
        if len(self.tags) > 50 or any(len(x) > 80 for x in self.tags):
            raise ValueError("标签超过编辑限制")
        return self


class BiographyInput(BaseModel):
    body: str = Field(min_length=1, max_length=100_000)
    summary: str | None = Field(default=None, max_length=2000)
    source_id: int | None = None


class CommentaryInput(BaseModel):
    body: str = Field(min_length=1, max_length=100_000)
    title: str | None = Field(default=None, max_length=500)
    commentator_name: str | None = Field(default=None, max_length=150)
    source_id: int | None = None


class TranslationInput(BaseModel):
    language_tag: str = Field(min_length=2, max_length=40)
    translator_name: str | None = Field(default=None, max_length=150)
    blocks: list[str] = Field(min_length=1, max_length=500)
    source_id: int | None = None

    @model_validator(mode="after")
    def validate_blocks(self):
        if any(not block.strip() or len(block) > 5000 for block in self.blocks):
            raise ValueError("译文分块不可为空或过长")
        return self


class ReviewInput(BaseModel):
    decision: Literal["approved", "rejected", "revoked"]
    legal_basis: str | None = None
    permitted_scope: str | None = None
    evidence_uri: str | None = None
    reason: str | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def require_evidence(self):
        if self.decision == "approved" and not all(
            item and item.strip() for item in (self.legal_basis, self.permitted_scope, self.evidence_uri)
        ):
            raise ValueError("批准公开必须填写权利依据、使用范围和证据位置")
        if self.decision == "approved" and self.permitted_scope != "web":
            raise ValueError("当前公开站点仅支持本站网页展示范围（web）")
        return self


class WorkDateInput(BaseModel):
    source_id: int | None = None
    year_start: int
    year_end: int
    date_precision: Literal["exact", "approximate", "range"]
    confidence: Literal["high", "medium", "low"]
    rationale: str | None = None
    review_status: Literal["pending", "reviewed", "rejected"] = "pending"
    is_preferred: bool = False

    @model_validator(mode="after")
    def ordered_years(self):
        if self.year_start > self.year_end:
            raise ValueError("创作年代起始年份不能大于结束年份")
        if self.is_preferred and self.review_status != "reviewed":
            raise ValueError("仅已核验时间可设为首选")
        return self


class AuthorEventInput(BaseModel):
    source_id: int | None = None
    year_start: int
    year_end: int
    date_precision: Literal["exact", "approximate", "range"]
    event_label: str = Field(min_length=1, max_length=2000)
    review_status: Literal["pending", "reviewed", "rejected"] = "pending"

    @model_validator(mode="after")
    def ordered_years(self):
        if self.year_start > self.year_end:
            raise ValueError("事件年代起始年份不能大于结束年份")
        return self


class PinyinToken(BaseModel):
    paragraph_index: int = Field(ge=0)
    char_index: int = Field(ge=0)
    character: str = Field(min_length=1, max_length=1)
    pinyin: str = Field(min_length=1, max_length=30)


class PinyinInput(BaseModel):
    tokens: list[PinyinToken] = Field(min_length=1, max_length=5000)
    romanization: str = Field(default="hanyu-pinyin", min_length=1, max_length=40)
    source_id: int | None = None
