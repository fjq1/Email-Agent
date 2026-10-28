"""约束只读检索输入，服务商原生语法在适配器中处理。"""

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
