"""生产配置；通过环境变量显式启用。"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("django-insecure-", "replace-")):  # noqa: F405
    raise ImproperlyConfigured("生产环境需要设置至少 50 字符的独立 DJANGO_SECRET_KEY。")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", default=False)  # noqa: F405
SECURE_HSTS_PRELOAD = env.bool("DJANGO_HSTS_PRELOAD", default=False)  # noqa: F405
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# 仅供 Django 自身的系统通知使用，和用户邮箱 OAuth 适配器分开。
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env("SYSTEM_SMTP_HOST", default="localhost"),  # noqa: F405
            "port": env.int("SYSTEM_SMTP_PORT", default=587),  # noqa: F405
            "username": env("SYSTEM_SMTP_USERNAME", default=""),  # noqa: F405
            "password": env("SYSTEM_SMTP_PASSWORD", default=""),  # noqa: F405
            "use_tls": env.bool("SYSTEM_SMTP_USE_TLS", default=True),  # noqa: F405
            "use_ssl": env.bool("SYSTEM_SMTP_USE_SSL", default=False),  # noqa: F405
            "timeout": 10,
        },
    }
}
# 仅在可信反向代理已经正确处理该请求头时启用。
if env.bool("DJANGO_TRUST_PROXY_SSL_HEADER", default=False):  # noqa: F405
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
