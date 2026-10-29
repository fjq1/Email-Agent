"""邮件检索、分析与人工审核的输入校验，服务端统一验证地址、日期和版本。"""

import uuid
from email.utils import getaddresses
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.core.validators import validate_email

from email_agent.models import TaskItem


class MailSearchForm(forms.Form):
    query = forms.CharField(
        label="检索条件",
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "发件人、主题或关键词"}),
    )
    page_size = forms.TypedChoiceField(
        label="每页数量",
        choices=[(10, "10 封"), (20, "20 封")],
        coerce=int,
        initial=10,
        required=False,
        empty_value=10,
    )
    cursor = forms.CharField(max_length=20000, required=False, widget=forms.HiddenInput)


class MailAnalysisForm(forms.Form):
    timezone_name = forms.CharField(
        label="日期解释时区",
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "例如 Asia/Shanghai"}),
    )

    def clean_timezone_name(self):
        value = self.cleaned_data["timezone_name"]
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise forms.ValidationError(
                "请填写有效时区，例如 Asia/Shanghai 或 America/New_York。"
            ) from None
        return value


class VersionedForm(forms.Form):
    # 隐藏版本号用于拒绝旧页面的保存与审批，不把浏览器输入当作最终批准内容。
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class DraftRevisionForm(VersionedForm):
    """每次打开页面分配一次请求标识，重复提交不会多次调用模型。"""

    instruction = forms.CharField(
        label="告诉助理如何修改",
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "例如：再简洁一些，保留会议时间；称呼和结尾也改成中文。",
            }
        ),
    )
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class ReplyContentForm(VersionedForm):
    to = forms.CharField(label="收件人", max_length=3000, help_text="多个邮箱用英文逗号分隔。")
    cc = forms.CharField(label="抄送", max_length=3000, required=False)
    subject = forms.CharField(label="主题", max_length=998)
    body = forms.CharField(label="回复正文", max_length=20000, widget=forms.Textarea)

    @staticmethod
    def addresses(value):
        if any(char in value for char in "\r\n\x00"):
            raise forms.ValidationError("邮箱地址中不能包含换行。")
        result = []
        for _, address in getaddresses([value]):
            validate_email(address)
            if address not in result:
                result.append(address)
        if len(result) > 20:
            raise forms.ValidationError("单次回复最多 20 个收件人或抄送。")
        return result

    def clean_to(self):
        value = self.addresses(self.cleaned_data["to"])
        if not value:
            raise forms.ValidationError("请至少填写一个有效收件人。")
        return value

    def clean_cc(self):
        return self.addresses(self.cleaned_data["cc"]) if self.cleaned_data["cc"] else []

    def clean_subject(self):
        value = self.cleaned_data["subject"]
        if any(char in value for char in "\r\n\x00"):
            raise forms.ValidationError("主题中不能包含换行。")
        return value

    def content(self):
        return {key: self.cleaned_data[key] for key in ("to", "cc", "subject", "body")}


class TaskContentForm(VersionedForm):
    title = forms.CharField(label="待办标题", max_length=300)
    description = forms.CharField(
        label="说明", max_length=5000, required=False, widget=forms.Textarea
    )
    responsible_name = forms.CharField(label="负责人称呼", max_length=200, required=False)
    responsible_email = forms.EmailField(label="负责人邮箱", max_length=320, required=False)
    due_date = forms.DateField(
        label="截止日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    due_time = forms.TimeField(
        label="截止时间",
        required=False,
        input_formats=["%H:%M"],
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )
    timezone_name = forms.CharField(
        label="日期解释时区",
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "例如 Asia/Shanghai"}),
    )
    dependency_notes = forms.CharField(
        label="依赖说明", max_length=5000, required=False, widget=forms.Textarea
    )
    dependencies = forms.ModelMultipleChoiceField(
        label="关联已有前置待办", queryset=TaskItem.objects.none(), required=False
    )

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dependencies"].queryset = TaskItem.objects.filter(owner=owner).exclude(
            status=TaskItem.Status.CANCELLED
        )

    def clean_timezone_name(self):
        return MailAnalysisForm.clean_timezone_name(self)

    def clean(self):
        data = super().clean()
        if data.get("due_time") and not data.get("due_date"):
            self.add_error("due_date", "填写截止时间时，也需要明确日期。")
        return data

    def content(self):
        data = {
            key: self.cleaned_data[key]
            for key in (
                "title",
                "description",
                "responsible_name",
                "responsible_email",
                "timezone_name",
                "dependency_notes",
            )
        }
        data["due_date"] = (
            self.cleaned_data["due_date"].isoformat() if self.cleaned_data["due_date"] else None
        )
        data["due_time"] = (
            self.cleaned_data["due_time"].strftime("%H:%M")
            if self.cleaned_data["due_time"]
            else None
        )
        data["dependencies"] = [str(item.pk) for item in self.cleaned_data["dependencies"]]
        return data
