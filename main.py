"""Generate and validate a set of study questions for one topic."""

from __future__ import annotations

import json
import re
import random
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from client import get_client, get_model
from observability import Timer, estimate_cost, estimate_tokens, log_call


TEMPERATURE = 0.2
MAX_TOKENS = 1800
MAX_ERROR_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 8.0


OptionText = Annotated[str, Field(..., min_length=1, max_length=200)]


class Question(BaseModel):
    """One validated study question."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=10, max_length=300)
    options: list[OptionText] = Field(..., min_length=4, max_length=4)
    answer: str = Field(..., min_length=1)
    explanation: str = Field(..., min_length=10, max_length=300)

    @field_validator("question", "answer", "explanation")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @field_validator("options")
    @classmethod
    def non_blank_options(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("options must not be blank")
        return values

    @model_validator(mode="after")
    def answer_matches_option(self) -> "Question":
        if self.answer not in self.options:
            raise ValueError("answer must exactly match one of the options")
        return self


class StudyQuestions(BaseModel):
    """The complete model response."""

    model_config = ConfigDict(extra="forbid")

    questions: list[Question] = Field(..., min_length=5, max_length=5)


_REFUSAL_PHRASES = (
    "i'm sorry, but i can't help with that. i'm not able to generate questions on this topic.",
    "i'm sorry, but i can't help with that",
    "i cannot help with that",
    "i am not able to generate questions on this topic",
)

_FENCE_PATTERN = re.compile(
    r"```[ \t]*(?:json[ \t]*)?\n?(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


def _normalized_text(value: object) -> str:
    return " ".join(str(value).split()).casefold()


def is_refusal(value: object) -> bool:
    """Return whether a model response explicitly refuses the request."""
    normalized = _normalized_text(value)
    return any(phrase in normalized for phrase in _REFUSAL_PHRASES)


def extract_json(response: object) -> dict[str, Any] | None:
    """Extract one JSON object from plain, fenced, or preamble-wrapped output."""
    if not isinstance(response, str):
        return None

    text = response.lstrip("\ufeff").strip()
    if not text:
        return None

    decoder = json.JSONDecoder()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return value if isinstance(value, dict) else None

    fence_matches = list(_FENCE_PATTERN.finditer(text))
    if fence_matches:
        for match in fence_matches:
            candidate = match.group(1).strip()
            if not candidate:
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            return value if isinstance(value, dict) else None
        return None

    stripped = text.lstrip()
    if stripped.startswith("["):
        return None
    if stripped.startswith("{"):
        try:
            value, _ = decoder.raw_decode(stripped)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    return None


def _messages(topic: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a study-question generator. Return only a valid JSON object. "
                "Do not return Markdown, code fences, preamble, or explanatory text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Generate exactly 5 study questions about this topic: {topic}\n\n"
                "Return a JSON object with exactly one key, \"questions\". The value "
                "must be an array of exactly 5 objects. Each object must contain "
                "exactly these keys: \"question\", \"options\", \"answer\", and "
                "\"explanation\". \"question\" and \"explanation\" must be strings "
                "between 10 and 300 characters. \"options\" must be an array of "
                "exactly 4 non-empty strings, each between 1 and 200 characters. "
                "\"answer\" must be a non-empty string that exactly matches one of "
                "the four options."
            ),
        },
    ]


def _call_model(messages: list[dict[str, str]]) -> object:
    client = get_client()
    # Keep SDK-level retries off so every network attempt is visible in our log.
    if hasattr(client, "with_options"):
        client = client.with_options(max_retries=0)
    return client.chat.completions.create(
        model=get_model(),
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )


def _response_content(response: object) -> object:
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError):
        return None


def _usage_counts(response: object, messages: list[dict[str, str]], content: object):
    """Return token counts and whether estimation was needed."""
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    measured = (
        isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool)
        and isinstance(completion_tokens, int) and not isinstance(completion_tokens, bool)
        and prompt_tokens > 0 and completion_tokens > 0
    )
    if measured:
        return prompt_tokens, completion_tokens, False

    prompt_text = "\n".join(str(message.get("content", "")) for message in messages)
    reply_text = content if isinstance(content, str) else ""
    return estimate_tokens(prompt_text), estimate_tokens(reply_text), True


def _retry_after_seconds(error: Exception) -> float | None:
    """Read a numeric or HTTP-date Retry-After value when the client exposes it."""
    value = getattr(error, "retry_after", None)
    if value is None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                value = headers.get("retry-after") or headers.get("Retry-After")
            except (AttributeError, TypeError):
                value = None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _wait_before_retry(error: Exception, retry_number: int) -> None:
    delay = min(
        MAX_RETRY_DELAY_SECONDS,
        (2 ** retry_number) + random.uniform(0, 1),
    )
    retry_after = _retry_after_seconds(error)
    if retry_after is not None and retry_after > delay:
        delay = min(MAX_RETRY_DELAY_SECONDS, retry_after)
    time.sleep(delay)


def _classify(content: object) -> tuple[str, dict[str, Any] | None]:
    if is_refusal(content):
        return "refused", None
    try:
        payload = extract_json(content)
        if payload is None:
            return "invalid_output", None
        StudyQuestions.model_validate(payload)
        return "ok", payload
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return "invalid_output", None


def _call_and_log_with_error_retries(
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, Any] | None, object]:
    """Make one logical request, retrying call exceptions up to three attempts."""
    model = get_model()
    for attempt in range(MAX_ERROR_ATTEMPTS):
        response = None
        try:
            with Timer() as timer:
                response = _call_model(messages)
        except Exception as error:
            log_call(
                status="error",
                model=model,
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=timer.ms,
                cost_usd=None,
                tokens_estimated=False,
            )
            if attempt + 1 >= MAX_ERROR_ATTEMPTS:
                return "error", None, None
            _wait_before_retry(error, attempt)
            continue

        content = _response_content(response)
        status, payload = _classify(content)
        prompt_tokens, completion_tokens, estimated = _usage_counts(
            response, messages, content
        )
        used_model = getattr(response, "model", None) or model
        log_call(
            status=status,
            model=used_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=timer.ms,
            cost_usd=estimate_cost(prompt_tokens, completion_tokens),
            tokens_estimated=estimated,
        )
        return status, payload, content

    return "error", None, None


def main() -> int:
    if len(sys.argv) != 2:
        print("error")
        return 2

    raw_topic = sys.argv[1]
    if not isinstance(raw_topic, str):
        print("error")
        return 2

    topic = raw_topic.strip()
    if not topic:
        print("error")
        return 2

    messages = _messages(topic)
    status, _payload, content = _call_and_log_with_error_retries(messages)
    if status == "invalid_output":
        repair_messages = [*messages]
        repair_messages.append({"role": "assistant", "content": str(content or "")})
        repair_messages.append({
            "role": "user",
            "content": (
                "Repair your previous response. Return only one JSON object that "
                "conforms exactly to the requested schema. Do not include a "
                "preamble or Markdown code fence."
            ),
        })
        status, _payload, _content = _call_and_log_with_error_retries(repair_messages)

    print(status)
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
