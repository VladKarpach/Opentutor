"""Parsing of structured model replies.

The learner-facing failure this protects against: a comprehension answer on a
programming topic came back as perfectly valid JSON, but the re-teach inside it
contained a Markdown code fence. The parser looked for a fenced block first and
cut the object apart at that fence, so every answer failed with "Could not
analyse the answer" after three retries.
"""

import json

import pytest

from services.adaptive_learning import content
from services.adaptive_learning.content import ContentGenerationError, extract_json


def _reply_with_fence() -> str:
    return json.dumps(
        {
            "units": [
                {"id": "u1", "status": "misconception", "why": "смешаны моменты"}
            ],
            "reteach": {
                "explanation": (
                    "Вот пример:\n```python\ndef g():\n    print('A')\n    yield 1\n```\n"
                    "Вызов g() ничего не печатает."
                )
            },
        },
        ensure_ascii=False,
    )


class TestExtractJson:
    def test_valid_object_with_code_fence_inside_a_string(self):
        """A fenced code sample inside a string value must not break parsing."""
        parsed = extract_json(_reply_with_fence())
        assert parsed["units"][0]["status"] == "misconception"
        assert "```python" in parsed["reteach"]["explanation"]

    def test_object_followed_by_prose_with_stray_braces(self):
        """Braces in trailing prose must not widen the object span."""
        text = '{"units": [{"id": "u1", "status": "partial"}]}\nЗаметка: {не json}'
        parsed = extract_json(text)
        assert parsed["units"][0]["id"] == "u1"

    def test_object_wrapped_in_a_fenced_block(self):
        text = (
            "Вот результат:\n```json\n"
            + json.dumps({"units": [{"id": "u2"}]})
            + "\n```\n"
        )
        parsed = extract_json(text)
        assert parsed["units"][0]["id"] == "u2"

    def test_prose_with_code_before_the_object(self):
        """A code sample outside the object must not be mistaken for it."""
        text = "Сначала пример:\n```python\nx = {'a': 1}\n```\nИтог: " + json.dumps(
            {"units": [{"id": "u3"}]}
        )
        parsed = extract_json(text)
        assert parsed["units"][0]["id"] == "u3"

    def test_empty_reply_is_rejected(self):
        with pytest.raises(ContentGenerationError):
            extract_json("")

    def test_reply_without_an_object_is_rejected(self):
        with pytest.raises(ContentGenerationError):
            extract_json("Не могу помочь с этим запросом.")


class _ScriptedClient:
    """Answers with a fixed reply and records how many calls were made."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    async def chat(self, system_prompt, user_message, images=None):
        self.calls += 1
        return self.reply, {"input_tokens": 0, "output_tokens": 0}


class TestStructuredCall:
    @pytest.mark.asyncio
    async def test_fenced_code_does_not_force_a_retry(self, monkeypatch):
        client = _ScriptedClient(_reply_with_fence())
        monkeypatch.setattr(content, "get_llm_client", lambda model=None: client)

        payload = await content.structured_call("system", "user")

        assert payload["units"][0]["id"] == "u1"
        assert client.calls == 1
