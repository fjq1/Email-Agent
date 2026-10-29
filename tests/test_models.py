"""检查平台隔离、缺失配置和接口参数，不访问真实模型 API。"""

import json
from copy import deepcopy
from unittest.mock import patch

import httpx2
import openai
import pytest
from django.core.exceptions import ImproperlyConfigured
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from email_agent.intelligence.models import get_chat_model, mail_model_extra_body, provider_statuses


@pytest.fixture
def configured_providers(settings):
    providers = deepcopy(settings.MODEL_PROVIDERS)
    for name, config in providers.items():
        config["api_key"] = f"test-key-for-{name}"
    settings.MODEL_PROVIDERS = providers
    return providers


@pytest.mark.parametrize("provider", ["bailian", "deepseek", "openai"])
def test_real_model_clients_initialize_without_network(configured_providers, provider):
    model = get_chat_model(provider)
    assert isinstance(model, ChatDeepSeek if provider == "deepseek" else ChatOpenAI)
    if provider == "deepseek":
        assert model.api_base == configured_providers[provider]["base_url"]
    else:
        assert model.openai_api_base == configured_providers[provider]["base_url"]
        assert model.use_responses_api is (provider == "openai")


def test_platform_credentials_do_not_cross(configured_providers):
    with patch("email_agent.intelligence.models.ChatDeepSeek") as constructor:
        get_chat_model("deepseek")
    assert constructor.call_args.kwargs["api_key"] == "test-key-for-deepseek"
    assert "temperature" not in constructor.call_args.kwargs


def test_missing_selected_key_fails_clearly(settings, configured_providers):
    settings.MODEL_PROVIDERS["bailian"]["api_key"] = ""
    with pytest.raises(ImproperlyConfigured, match="API Key"):
        get_chat_model("bailian")
    assert get_chat_model("deepseek")


def test_unknown_provider_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="平台"):
        get_chat_model("unknown")


def test_insecure_or_credential_bearing_endpoint_is_rejected(settings, configured_providers):
    settings.MODEL_PROVIDERS["bailian"]["base_url"] = "http://example.com/v1"
    with pytest.raises(ImproperlyConfigured, match="HTTPS"):
        get_chat_model("bailian")


def test_public_status_never_contains_credentials(configured_providers, settings):
    assert "test-key-for" not in str(provider_statuses())
    settings.MAIL_ANALYSIS_PROVIDER = "bailian"
    settings.MAIL_ANALYSIS_EXTRA_BODY = {"enable_thinking": False}
    assert mail_model_extra_body("bailian") == {"enable_thinking": False}
    assert mail_model_extra_body("deepseek") == mail_model_extra_body("openai") == {}
    configured_providers["openai"]["model"] = ""
    assert not provider_statuses()[2]["configured"]


@pytest.mark.parametrize(
    "provider,path",
    [
        ("bailian", "/compatible-mode/v1/chat/completions"),
        ("deepseek", "/chat/completions"),
        ("openai", "/v1/responses"),
    ],
)
def test_model_request_protocol_without_network(configured_providers, provider, path):
    """模拟 HTTP 响应，验证平台地址、密钥、模型名和请求协议。"""
    captured = []

    def respond(request):
        captured.append(request)
        if provider == "openai":
            payload = {
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": configured_providers[provider]["model"],
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
                    }
                ],
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
            }
        else:
            payload = {
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "created": 1,
                "model": configured_providers[provider]["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK"},
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            }
        return httpx2.Response(200, json=payload)

    original_client = openai.OpenAI
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as http_client:

        def isolated_client(**options):
            options["http_client"] = http_client
            return original_client(**options)

        with patch("openai.OpenAI", side_effect=isolated_client):
            response = get_chat_model(provider).invoke("只回复 OK")
    assert "OK" in str(response.content)
    assert len(captured) == 1
    assert captured[0].url.path == path
    assert captured[0].headers["authorization"] == f"Bearer test-key-for-{provider}"
    body = json.loads(captured[0].content)
    assert body["model"] == configured_providers[provider]["model"]
    if provider == "openai":
        assert body["store"] is False
