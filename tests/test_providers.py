import json

import httpx
import pytest

from akemi.brain.providers import AnthropicProvider, Message, OpenAICompatibleProvider, ProviderError, build_provider
from akemi.core.config import Settings

MSGS = [Message("system", "sys"), Message("user", "oi")]


def client(handler, base="https://x/v1"):
    return httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_openai_compatible_complete_and_json_fallback():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": " olá "}}]})

    p = OpenAICompatibleProvider("https://x/v1", client=client(handler))
    assert await p.complete(MSGS, model="m", json_mode=True) == "olá"
    assert len(seen) == 2 and seen[0]["messages"][1] == {"role": "user", "content": "oi"}


@pytest.mark.asyncio
async def test_openai_compatible_stream():
    sse = "".join(
        f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in ["Oi", ", ", "Mateus"]
    ) + "data: [DONE]\n\n"
    p = OpenAICompatibleProvider("https://x/v1", client=client(lambda r: httpx.Response(200, text=sse)))
    assert "".join([c async for c in p.stream(MSGS, model="m")]) == "Oi, Mateus"


@pytest.mark.asyncio
async def test_provider_error_wraps_http():
    p = OpenAICompatibleProvider("https://x/v1", client=client(lambda r: httpx.Response(500)))
    with pytest.raises(ProviderError):
        await p.complete(MSGS, model="m")


@pytest.mark.asyncio
async def test_anthropic_moves_system_prompt():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "oi!"}]})

    p = AnthropicProvider("k", client=client(handler, "https://a/v1"))
    assert await p.complete(MSGS, model="claude") == "oi!"
    assert seen["system"] == "sys" and seen["messages"] == [{"role": "user", "content": "oi"}]


def test_build_provider_requires_nim_key():
    with pytest.raises(ProviderError):
        build_provider("nim", Settings(_env_file=None, nvidia_api_key=""))
    assert build_provider("echo", Settings(_env_file=None)).name == "echo"
