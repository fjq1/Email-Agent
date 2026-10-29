"""按平台创建模型，启动 Web 服务时不会调用模型或要求所有密钥齐全。"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

PROVIDER_LABELS = {"bailian": "阿里云百炼", "deepseek": "DeepSeek 官方", "openai": "OpenAI / GPT"}


def get_chat_model(provider: str | None = None, *, extra_body: dict | None = None) -> BaseChatModel:
    """密钥、地址和模型名称均使用所选平台的独立配置。"""
    provider = provider or settings.LLM_DEFAULT_PROVIDER
    if provider not in settings.MODEL_PROVIDERS:
        raise ImproperlyConfigured("模型平台必须是 bailian、deepseek 或 openai。")
    config = settings.MODEL_PROVIDERS[provider]
    if not config["api_key"].strip():
        raise ImproperlyConfigured(f"尚未配置 {PROVIDER_LABELS[provider]} 的 API Key。")
    if not config["model"].strip():
        raise ImproperlyConfigured("请配置所选平台的模型名称。")
    url = urlsplit(config["base_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ImproperlyConfigured("模型接口必须使用不包含账号密码的 HTTPS 地址。")
    if not isinstance(config["extra_body"], dict):
        raise ImproperlyConfigured("模型 EXTRA_BODY 必须是 JSON 对象。")
    if extra_body is not None and not isinstance(extra_body, dict):
        raise ImproperlyConfigured("调用场景的 EXTRA_BODY 必须是 JSON 对象。")
    options = {
        "model": config["model"],
        "api_key": config["api_key"],
        "timeout": settings.LLM_TIMEOUT_SECONDS,
        "max_retries": settings.LLM_MAX_RETRIES,
    }
    # 部分推理模型不接受 temperature，留空时完全省略该参数。
    if config["temperature"] is not None:
        options["temperature"] = config["temperature"]
    # 场景参数覆盖平台默认参数，例如邮件抽取关闭深度思考，不影响其他调用。
    merged_body = {**config["extra_body"], **(extra_body or {})}
    if merged_body:
        options["extra_body"] = merged_body
    if provider == "deepseek":
        return ChatDeepSeek(api_base=config["base_url"], **options)
    if provider == "openai":
        return ChatOpenAI(
            base_url=config["base_url"],
            use_responses_api=settings.OPENAI_USE_RESPONSES_API,
            store=False,
            **options,
        )
    # 百炼当前接入 Chat Completions 兼容协议，禁止误用 Responses API。
    return ChatOpenAI(base_url=config["base_url"], use_responses_api=False, **options)


def provider_configured(provider: str) -> bool:
    """可选状态仅检查本地配置是否齐全，不发请求验证密钥或模型权限。"""
    config = settings.MODEL_PROVIDERS.get(provider, {})
    return bool(config.get("api_key", "").strip() and config.get("model", "").strip())


def mail_model_extra_body(provider: str) -> dict:
    """旧的邮件场景参数只属于原默认平台，避免百炼参数被带到其他接口。"""
    return settings.MAIL_ANALYSIS_EXTRA_BODY if provider == settings.MAIL_ANALYSIS_PROVIDER else {}


def provider_statuses(selected: str | None = None) -> list[dict]:
    """只向页面暴露配置状态，不返回密钥和接口地址。"""
    return [
        {
            "name": name,
            "label": PROVIDER_LABELS[name],
            "model": config["model"],
            "configured": provider_configured(name),
            "default": name == (selected or settings.LLM_DEFAULT_PROVIDER),
        }
        for name, config in settings.MODEL_PROVIDERS.items()
    ]
