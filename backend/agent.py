"""The model client for the paid web search (backend/websearch.py): OpenRouter only, key from OPENROUTER_API_KEY.
A small part of Mika's backend/agent.py; the dashboard's chat builds its own free-model client in elm/chat.py."""
import os

from openai import AsyncOpenAI

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-5-nano")
SEARCH_MODEL = os.getenv("SEARCH_MODEL") or None
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def client(timeout: float = 60, retries: int = 1) -> AsyncOpenAI:
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return AsyncOpenAI(api_key=key, base_url=OPENROUTER_BASE_URL, timeout=timeout, max_retries=retries,
                       default_headers={"HTTP-Referer": "https://mika-jobs.onrender.com", "X-Title": "Mika Job Dashboard"})


def search_tool() -> dict:
    return {"type": "openrouter:web_search"}


def model_id(name: str, search: bool = False) -> str:
    return name if "/" in name else "openai/" + name
