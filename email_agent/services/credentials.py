"""授权数据仅以密文落库；加密密钥应与数据库备份分开保管。"""

import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from email_agent.providers.base import MailError


def credential_cipher():
    try:
        return Fernet(settings.MAIL_TOKEN_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, UnicodeError):
        raise MailError(
            "请先在 .env 配置有效的 MAIL_TOKEN_ENCRYPTION_KEY。", "encryption_not_configured"
        ) from None


def encrypt_credentials(data):
    return credential_cipher().encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def decrypt_credentials(value):
    try:
        data = json.loads(credential_cipher().decrypt(value.encode("ascii")))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (InvalidToken, ValueError, UnicodeError):
        raise MailError(
            "无法读取授权凭据，请检查加密密钥或重新连接邮箱。", "credentials_unreadable"
        ) from None
