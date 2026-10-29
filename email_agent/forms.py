"""邮件检索与分析入口的输入校验，原生搜索语法由适配器处理。"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms


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
