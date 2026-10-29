"""模型输出契约：限制长度与数量，原文证据由业务层再次定位验证。"""

from datetime import date, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StructuredData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(StructuredData):
    field: Literal["subject", "body_text", "sender", "to_recipients", "cc_recipients"]
    quote: str = Field(
        min_length=1, max_length=600, description="逐字复制原文，不得改写或使用省略号"
    )


class Finding(StructuredData):
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)


class Deadline(StructuredData):
    raw: str = Field(min_length=1, max_length=200, description="日期原始说法，必须出现在日期证据中")
    date: str | None = Field(default=None, description="可确定时填写 YYYY-MM-DD，否则为 null")
    time: str | None = Field(default=None, description="明确提及时填写 HH:MM；不得自行补 23:59")
    timezone: str | None = Field(default=None, max_length=80)
    inferred: bool = Field(default=False, description="相对日期或补全年份属于推断，设为 true")
    evidence: list[Evidence] = Field(min_length=1, max_length=3)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("日期必须是 YYYY-MM-DD")
        return value

    @field_validator("time")
    @classmethod
    def valid_time(cls, value):
        if value is not None and (
            len(value) != 5 or time.fromisoformat(value).isoformat(timespec="minutes") != value
        ):
            raise ValueError("时间必须是 HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("时区必须是有效的 IANA 名称") from None
        return value


class ResponsiblePerson(StructuredData):
    raw: str = Field(min_length=1, max_length=200, description="保留原称呼，例如‘你’或‘项目负责人’")
    email: str | None = Field(
        default=None, max_length=320, description="证据中明确出现才可填写，不推测通讯录关系"
    )
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class TaskExtraction(StructuredData):
    title: str = Field(
        min_length=1, max_length=300, description="简短、具体且稳定的事项名称，不加入日期、催办字样"
    )
    description: str = Field(min_length=1, max_length=1500)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    owner: ResponsiblePerson | None = None
    deadline: Deadline | None = None
    dependencies: list[Finding] = Field(default_factory=list, max_length=10)
    recurring: bool = False
    occurrence: str | None = Field(
        default=None, max_length=100, description="周期任务的本次实例，如 2026-10；无法确定填 null"
    )
    uncertainties: list[str] = Field(default_factory=list, max_length=10)


class ReplyDraft(StructuredData):
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=6000)
    evidence: list[Evidence] = Field(min_length=1, max_length=5, description="需要回复的原文依据")
    uncertainties: list[str] = Field(default_factory=list, max_length=10)


class MailUnderstanding(StructuredData):
    primary_category: Literal["notification", "reply_required", "task_required"]
    requires_reply: bool
    contains_tasks: bool
    needs_review: bool
    classification_evidence: list[Evidence] = Field(min_length=1, max_length=5)
    summary: list[Finding] = Field(min_length=1, max_length=6)
    tasks: list[TaskExtraction] = Field(default_factory=list, max_length=15)
    reply_draft: ReplyDraft | None = None
    uncertainties: list[str] = Field(default_factory=list, max_length=15)
