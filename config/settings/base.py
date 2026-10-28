"""所有环境共用的配置；敏感信息仅从环境变量读取。"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
# 已有系统环境变量优先于本地文件，便于容器和持续集成注入配置。
environ.Env.read_env(BASE_DIR / ".env", overwrite=False, encoding="utf-8")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "email_agent.apps.EmailAgentConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="email_agent"),
        "USER": env("POSTGRES_USER", default="email_agent"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", default="127.0.0.1"),
        "PORT": env.int("POSTGRES_PORT", default=55432),
        "CONN_MAX_AGE": env.int("POSTGRES_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": env.int("POSTGRES_CONNECT_TIMEOUT", default=5),
            "sslmode": env("POSTGRES_SSLMODE", default="prefer"),
        },
    }
}
# 留空时使用 Django 当前数据库；测试时也会正确指向测试数据库。
LANGGRAPH_DATABASE_URL = env("LANGGRAPH_DATABASE_URL", default="")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = env("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LLM_DEFAULT_PROVIDER = env("LLM_DEFAULT_PROVIDER", default="bailian")
LLM_TIMEOUT_SECONDS = env.float("LLM_TIMEOUT_SECONDS", default=60)
LLM_MAX_RETRIES = env.int("LLM_MAX_RETRIES", default=2)


def model_configuration(prefix, default_model, default_base_url):
    """每个平台独立读取配置，避免不同平台共用密钥或地址。"""
    temperature = env(f"{prefix}_TEMPERATURE", default="")
    return {
        "api_key": env(f"{prefix}_API_KEY", default=""),
        "model": env(f"{prefix}_MODEL", default=default_model),
        "base_url": env(f"{prefix}_BASE_URL", default=default_base_url),
        "temperature": float(temperature) if temperature else None,
        "extra_body": env.json(f"{prefix}_EXTRA_BODY", default={}),
    }


MODEL_PROVIDERS = {
    "bailian": model_configuration(
        "DASHSCOPE", "qwen-plus", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ),
    "deepseek": model_configuration("DEEPSEEK", "deepseek-flash", "https://api.deepseek.com"),
    "openai": model_configuration("OPENAI", "gpt-5.5", "https://api.openai.com/v1"),
}
OPENAI_USE_RESPONSES_API = env.bool("OPENAI_USE_RESPONSES_API", default=True)

# 邮箱授权与模型 API 完全独立。未配置 OAuth 时仍可登录并查看配置指引。
MAIL_TOKEN_ENCRYPTION_KEY = env("MAIL_TOKEN_ENCRYPTION_KEY", default="")
MAIL_HTTP_TIMEOUT = env.float("MAIL_HTTP_TIMEOUT", default=15)
MAIL_MAX_BODY_CHARS = env.int("MAIL_MAX_BODY_CHARS", default=200000)
MAIL_MAX_RESPONSE_BYTES = env.int("MAIL_MAX_RESPONSE_BYTES", default=32 * 1024 * 1024)
MAIL_OAUTH_PROVIDERS = {
    "gmail": {
        "client_id": env("GMAIL_CLIENT_ID", default=""),
        "client_secret": env("GMAIL_CLIENT_SECRET", default=""),
        "redirect_uri": env(
            "GMAIL_REDIRECT_URI", default="http://localhost:8000/mailboxes/oauth/gmail/callback/"
        ),
    },
    "outlook": {
        "client_id": env("OUTLOOK_CLIENT_ID", default=""),
        "client_secret": env("OUTLOOK_CLIENT_SECRET", default=""),
        "redirect_uri": env(
            "OUTLOOK_REDIRECT_URI",
            default="http://localhost:8000/mailboxes/oauth/outlook/callback/",
        ),
        "tenant": env("OUTLOOK_TENANT_ID", default="common"),
    },
}

# Django 自身的通知邮件先输出到控制台；真实邮箱发送由后续适配器负责。
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "filters": {"oauth_secrets": {"()": "email_agent.logging.RedactOAuthQueryFilter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["oauth_secrets"],
        }
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    # HTTP 调试日志可能包含邮件正文和请求参数，默认关闭。
    "loggers": {
        # runserver 默认不向根日志传播，需要显式使用带授权参数脱敏的处理器。
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
}
