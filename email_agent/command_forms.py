"""指令、计划和偏好表单；所有邮箱选择都限定在当前登录用户内。"""

import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms

from email_agent.forms import VersionedForm
from email_agent.intelligence.commands import SearchFilters
from email_agent.models import MailboxAccount, UserPreference
from email_agent.services.preferences import LANGUAGES, TONES, validate_preference

ACTION_CHOICES = [
    ("search", "仅检索"),
    ("summarize", "生成摘要"),
    ("extract_tasks", "提取待办并送审"),
    ("draft_replies", "生成回复并送审"),
    ("compose_email", "撰写新邮件并送审"),
    ("preference_only", "仅提出偏好"),
    ("unsupported", "暂不支持，需修改计划"),
]


def clean_zone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise forms.ValidationError(
            "请填写有效时区，例如 Asia/Shanghai 或 America/New_York。"
        ) from None
    return value


class CommandStartForm(forms.Form):
    mailbox = forms.ModelChoiceField(
        label="本次处理的邮箱",
        queryset=MailboxAccount.objects.none(),
        empty_label="请选择本次使用的邮箱",
    )
    instruction = forms.CharField(
        label="你的指令",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="例如：总结最近7天关于项目进展的邮件，最多5封。",
    )
    timezone_name = forms.CharField(
        label="日期解释时区",
        max_length=80,
        required=False,
        help_text="留空使用该邮箱的已确认默认时区；没有偏好时使用应用默认值。",
    )
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mailbox"].queryset = MailboxAccount.objects.filter(
            owner=owner, status="connected"
        )

    def clean_timezone_name(self):
        value = self.cleaned_data["timezone_name"]
        return clean_zone(value) if value else ""


class CommandSpecForm(VersionedForm):
    action = forms.ChoiceField(
        label="处理目标", choices=[item for item in ACTION_CHOICES if item[0] != "compose_email"]
    )
    folder = forms.ChoiceField(
        label="文件夹范围",
        choices=[("inbox", "收件箱"), ("sent", "已发送"), ("all", "服务商默认全邮箱范围")],
    )
    sender = forms.CharField(label="发件人姓名或邮箱", max_length=120, required=False)
    recipient = forms.CharField(label="收件人姓名或邮箱", max_length=120, required=False)
    subject = forms.CharField(label="主题包含", max_length=120, required=False)
    keywords = forms.CharField(
        label="正文或邮件关键词",
        max_length=484,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="每行一个，最多4个，同时匹配。",
    )
    date_from = forms.DateField(
        label="收到日期起", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label="收到日期止（含当日）", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    unread = forms.ChoiceField(
        label="已读状态", choices=[("all", "不限"), ("unread", "未读"), ("read", "已读")]
    )
    limit = forms.IntegerField(label="本次最多处理邮件数", min_value=1, max_value=10)
    timezone_name = forms.CharField(label="日期解释时区", max_length=80)
    reply_language = forms.ChoiceField(
        label="本次回复语言", choices=[("", "使用已确认偏好"), *LANGUAGES.items()], required=False
    )
    reply_tone = forms.ChoiceField(
        label="本次回复风格", choices=[("", "使用已确认偏好"), *TONES.items()], required=False
    )

    def clean_timezone_name(self):
        return clean_zone(self.cleaned_data["timezone_name"])

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        if data["action"] == "unsupported":
            raise forms.ValidationError("请先选择支持的动作，并手动核对当前检索范围。")
        try:
            SearchFilters.model_validate(self.filter_values())
        except ValueError:
            raise forms.ValidationError(
                "请检查日期顺序与查询文字；关键词最多4个，不能含引号、反斜杠或换行。"
            ) from None
        return data

    def filter_values(self):
        data = self.cleaned_data
        return {
            key: data[key]
            for key in ("folder", "sender", "recipient", "subject", "date_from", "date_to")
        } | {
            "keywords": [value.strip() for value in data["keywords"].splitlines() if value.strip()],
            "unread": {"all": None, "unread": True, "read": False}[data["unread"]],
        }

    def spec(self, original):
        return {
            **original,
            "action": self.cleaned_data["action"],
            "limit": self.cleaned_data["limit"],
            "filters": SearchFilters.model_validate(self.filter_values()).model_dump(mode="json"),
            "options": {
                key: self.cleaned_data[key] or None for key in ("reply_language", "reply_tone")
            },
            "clarifications": [],
            "explanation": "用户已手动核对并保存当前范围。",
        }


class PreferenceForm(forms.Form):
    mailbox = forms.ModelChoiceField(
        label="适用范围",
        queryset=MailboxAccount.objects.none(),
        required=False,
        empty_label="所有邮箱的默认偏好",
    )
    key = forms.ChoiceField(label="偏好类型", choices=UserPreference.Key.choices)
    value = forms.CharField(
        label="偏好内容",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="时区填 IANA 名称；语言填 auto/zh/en；风格填 professional/friendly/concise；签名直接填写文本。",
    )
    version = forms.IntegerField(min_value=0, initial=0, widget=forms.HiddenInput)

    def __init__(self, *args, owner, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mailbox"].queryset = MailboxAccount.objects.filter(owner=owner)
        key = (
            item.key
            if item
            else self.data.get("key")
            if self.is_bound
            else self.initial.get("key", "timezone_name")
        )
        self.fields["key"].widget = forms.HiddenInput()
        if key == "reply_language":
            self.fields["value"] = forms.ChoiceField(label="回复语言", choices=LANGUAGES.items())
        elif key == "reply_tone":
            self.fields["value"] = forms.ChoiceField(label="回复风格", choices=TONES.items())
        elif key == "timezone_name":
            self.fields["value"] = forms.CharField(
                label="默认时区",
                max_length=80,
                help_text="例如 Asia/Shanghai 或 America/New_York。",
            )
        if item:
            self.initial.update(
                mailbox=item.mailbox_id, key=item.key, value=item.value, version=item.version
            )
            # 修改已有偏好时不偷偷移动范围，其他范围需要另建一项。
            self.fields["mailbox"].disabled = self.fields["key"].disabled = True

    def clean(self):
        data = super().clean()
        if "key" in data and "value" in data:
            data["value"] = validate_preference(data["key"], data["value"])
        return data
