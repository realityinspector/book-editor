import asyncio
import logging
import json
from openai import AsyncOpenAI

from book_editor.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 15]  # seconds


def get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        default_headers={
            "HTTP-Referer": "https://book-editor.local",
            "X-OpenRouter-Title": settings.app_name,
        },
    )


async def chat(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> str:
    """Send a chat completion request to OpenRouter with retry logic. Returns the assistant's text."""
    client = get_client()
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if response_format:
        kwargs["response_format"] = response_format

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            logger.info(f"LLM call: model={model} input_tokens={resp.usage.prompt_tokens} output_tokens={resp.usage.completion_tokens}")
            return content
        except Exception as e:
            last_error = e
            error_code = getattr(e, "status_code", 0)
            # Retry on rate limits (429) and server errors (5xx)
            if error_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(f"LLM call failed (attempt {attempt + 1}/{MAX_RETRIES}): {error_code} {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            logger.error(f"LLM call failed: model={model} error={e}")
            raise

    raise last_error


async def chat_json(
    model: str,
    messages: list[dict],
    temperature: float = 0.4,
    max_tokens: int | None = None,
) -> dict:
    """Chat expecting JSON output. Parses the response."""
    raw = await chat(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    return json.loads(text)


async def chat_stream(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
):
    """Stream chat completion. Yields content chunks."""
    client = get_client()
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
