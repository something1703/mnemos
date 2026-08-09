"""OpenAIChatClient against a mocked HTTP layer — no network, no cost.

Three things get dedicated coverage here that the golden eval (which only
ever sees a healthy, real API) cannot exercise: the JSON-repair path, the
temperature-unsupported detection this project found empirically against
gpt-5.6-luna, and retry exhaustion.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest
from mnemos_engine.llm import LLMError, OpenAIChatClient


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _completion(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _temperature_error() -> urllib.error.HTTPError:
    body = json.dumps(
        {
            "error": {
                "message": "Unsupported value: 'temperature' does not support 0 with this model.",
                "param": "temperature",
                "code": "unsupported_value",
            }
        }
    ).encode()
    error = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)
    error.read = lambda: body  # type: ignore[method-assign]
    return error


@pytest.fixture(autouse=True)
def _no_real_sleep() -> None:
    with patch("mnemos_engine.llm.time.sleep"):
        yield


async def test_complete_json_success() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_completion('[{"a": 1}]'))):
        result = await client.complete_json(system="sys", user="usr")
    assert result == [{"a": 1}]


async def test_complete_json_strips_markdown_fence() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    fenced = '```json\n[{"a": 1}]\n```'
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_completion(fenced))):
        result = await client.complete_json(system="sys", user="usr")
    assert result == [{"a": 1}]


async def test_complete_json_repairs_once_on_malformed_response() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    responses = [
        _FakeResponse(_completion("not json at all")),
        _FakeResponse(_completion('{"fixed": true}')),
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        result = await client.complete_json(system="sys", user="usr")
    assert result == {"fixed": True}


async def test_complete_json_raises_after_failed_repair() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    responses = [
        _FakeResponse(_completion("not json")),
        _FakeResponse(_completion("still not json")),
    ]
    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(LLMError, match="repair attempt"),
    ):
        await client.complete_json(system="sys", user="usr")


async def test_temperature_unsupported_is_detected_and_remembered() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-5.6-luna")
    responses = [_temperature_error(), _FakeResponse(_completion('{"ok": true}'))]
    with patch("urllib.request.urlopen", side_effect=responses):
        result = await client.complete_json(system="sys", user="usr", temperature=0.0)
    assert result == {"ok": True}
    assert client._temperature_unsupported is True

    # Second call: no retry needed, temperature omitted from the very first attempt.
    with patch(
        "urllib.request.urlopen", return_value=_FakeResponse(_completion('{"ok": true}'))
    ) as mocked:
        await client.complete_json(system="sys", user="usr", temperature=0.0)
    assert mocked.call_count == 1
    sent_body = json.loads(mocked.call_args.args[0].data)
    assert "temperature" not in sent_body


async def test_retries_exhausted_raises_llm_error() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    error = urllib.error.URLError("connection refused")
    with (
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(LLMError, match="could not reach"),
    ):
        await client.complete_json(system="sys", user="usr")


async def test_no_choices_in_response_raises() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    with (
        patch("urllib.request.urlopen", return_value=_FakeResponse({"choices": []})),
        pytest.raises(LLMError, match="no choices"),
    ):
        await client.complete_json(system="sys", user="usr")


async def test_empty_completion_content_raises() -> None:
    client = OpenAIChatClient(api_key="k", model="gpt-test")
    with (
        patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse({"choices": [{"message": {"content": ""}}]}),
        ),
        pytest.raises(LLMError, match="empty completion"),
    ):
        await client.complete_json(system="sys", user="usr")
