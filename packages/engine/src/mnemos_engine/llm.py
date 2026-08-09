"""A minimal OpenAI chat completions client, for the two places this system
asks a model a question rather than embedding text: distillation (Phase 05.1)
and contradiction judging (Phase 05.3).

Same reasoning as `OpenAIEmbedder`: `urllib` rather than the `openai` SDK, so
the dependency has nothing to drift into `packages/warden` with (`make
no-model-in-warden` enforces this at the import-graph level). It lives in the
engine, not the sleep cycle, because the engine is the workspace's one place
allowed to talk to a model provider — the sleep cycle imports it the same way
`services/api` imports `OpenAIEmbedder`.

`complete_json` is the interesting entry point. Every model call in this
system that writes into memory returns *structured data, not an action* — the
contradiction judge's verdict can set a trust field, but nothing here can ever
issue a query. That boundary is enforced by what the callers do with the
return value, not by this module, so this module's only job is to get valid
JSON back reliably: one repair retry if the model returns something that
doesn't parse, because a distillation run that silently drops a malformed
response degrades quietly and a run that crashes on one bad completion loses
the whole batch for a `{` that arrived truncated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

log = logging.getLogger("mnemos.llm")

_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 3


class LLMError(RuntimeError):
    """The model provider could not be reached, refused the request, or never
    produced parseable JSON even after a repair retry."""


class ChatClient(Protocol):
    """The one method distillation and belief revision need, as a Protocol
    rather than the concrete `OpenAIChatClient` — the same reasoning as
    `Embedder` in `embeddings.py`. Tests exercise the prompt-construction and
    JSON-validation logic in `distill.py`/`revise.py` against a scripted stub
    implementing this, with no network call and no cost, and no test needs to
    subclass a class that does HTTP in its constructor path."""

    async def complete_json(
        self, *, system: str, user: str, temperature: float = 0.0, max_output_tokens: int = 2048
    ) -> Any: ...


_TEMPERATURE_UNSUPPORTED = "does not support"


class OpenAIChatClient:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature_unsupported = False
        """Some reasoning-tuned models (gpt-5.6-luna among them, confirmed
        against the live API rather than assumed) reject any `temperature`
        other than their fixed default and answer HTTP 400 `unsupported_value`
        if one is sent. Detected once per client instance and remembered, so
        only the very first call on a new model pays the cost of finding out —
        every call after either omits the field or never needed to try."""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ) -> Any:
        """Ask for a JSON value and get one back, or raise `LLMError`.

        One repair attempt: if the first response fails `json.loads`, the model
        is shown its own output and the parse error, and asked to return
        corrected JSON and nothing else. A second failure raises — silently
        returning `None` would make an empty distillation indistinguishable
        from a session that genuinely contained no durable facts, and those
        need different handling downstream (the latter is normal; the former is
        a bug worth counting).
        """
        raw = await asyncio.to_thread(
            self._call_with_retry, system, user, temperature, max_output_tokens
        )
        try:
            return json.loads(_strip_fences(raw))
        except json.JSONDecodeError as exc:
            log.warning("model response was not valid JSON; attempting one repair")
            repair_user = (
                f"Your previous response could not be parsed as JSON.\n\n"
                f"Error: {exc}\n\nYour response was:\n{raw}\n\n"
                "Return ONLY corrected, valid JSON. No prose, no code fences."
            )
            repaired = await asyncio.to_thread(
                self._call_with_retry, system, repair_user, temperature, max_output_tokens
            )
            try:
                return json.loads(_strip_fences(repaired))
            except json.JSONDecodeError as second_exc:
                raise LLMError(
                    f"model did not return valid JSON after one repair attempt: {second_exc}"
                ) from second_exc

    def _build_payload(
        self, system: str, user: str, temperature: float, max_output_tokens: int
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_output_tokens,
        }
        if not self._temperature_unsupported:
            payload["temperature"] = temperature
        return payload

    def _call(self, system: str, user: str, temperature: float, max_output_tokens: int) -> str:
        payload = self._build_payload(system, user, temperature, max_output_tokens)
        body = json.dumps(payload).encode("utf-8")

        try:
            return self._post(body)
        except LLMError as exc:
            if (
                not self._temperature_unsupported
                and "temperature" in str(exc)
                and _TEMPERATURE_UNSUPPORTED in str(exc)
            ):
                # Not a transient failure — retrying with backoff would just
                # fail the same way three times. Drop the field and try once
                # immediately; every later call on this client skips straight
                # to the working payload.
                log.info(
                    "model %r rejects a custom temperature; omitting it from now on", self._model
                )
                self._temperature_unsupported = True
                payload = self._build_payload(system, user, temperature, max_output_tokens)
                body = json.dumps(payload).encode("utf-8")
                return self._post(body)
            raise

    def _call_with_retry(
        self, system: str, user: str, temperature: float, max_output_tokens: int
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return self._call(system, user, temperature, max_output_tokens)
            except LLMError as exc:
                last_error = exc
                if attempt == _MAX_ATTEMPTS:
                    raise
                delay = 1.0 * (2 ** (attempt - 1))
                log.warning(
                    "chat completion attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise LLMError(str(last_error))  # pragma: no cover - loop always returns or raises

    def _post(self, body: bytes) -> str:
        request = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                parsed = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"could not reach the chat completions endpoint: {exc}") from exc

        choices = parsed.get("choices") or []
        if not choices:
            raise LLMError(f"no choices in response: {parsed}")
        content = choices[0].get("message", {}).get("content")
        if not content:
            raise LLMError(f"empty completion content: {parsed}")

        usage = parsed.get("usage", {})
        log.debug(
            "chat completion: %s prompt + %s completion tokens",
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
        )
        return str(content)


def _strip_fences(text: str) -> str:
    """Strip a ```json ... ``` fence if the model added one despite being told
    not to. Cheap insurance — models do this often enough that handling it
    beats spending a repair round-trip on it."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped
