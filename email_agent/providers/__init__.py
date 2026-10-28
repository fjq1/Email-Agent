"""官方邮箱 API 的只读适配器。"""

from .base import MailError
from .gmail import GmailProvider
from .outlook import OutlookProvider

PROVIDERS = {"gmail": GmailProvider, "outlook": OutlookProvider}


def get_provider(name):
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise MailError("不支持的邮箱平台。", "unknown_provider") from None
