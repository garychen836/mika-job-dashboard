"""Token use and dollars per kind per day (elm_chat, elm_interview free; elm_search, elm_profile paid). File: _usage.json.
Prices come from Mika's own table (backend/stats.MODEL_PRICES), with a copy as fallback if that module cannot load."""
from __future__ import annotations
import os
from datetime import date

import durable

FILE = "_usage.json"
FREE_KINDS = ("elm_chat", "elm_interview")
_FALLBACK = {"openai/gpt-6-luna": (0.10, 0.01, 0.50), "openai/gpt-5-nano": (0.05, 0.005, 0.40)}
DEFAULT_SEARCH_MODEL = "openai/gpt-5-nano"


def prices() -> dict:
    try:
        import stats as mika_stats          # backend/stats.py
        return mika_stats.MODEL_PRICES
    except Exception:
        return _FALLBACK


def search_model() -> str:
    return (os.getenv("SEARCH_MODEL") or DEFAULT_SEARCH_MODEL).strip()


def profile_model() -> str:
    return (os.getenv("ELM_PROFILE_MODEL") or search_model()).strip()


def model_for(kind: str) -> str | None:
    return None if kind in FREE_KINDS else profile_model() if kind == "elm_profile" else search_model()


def record(inp: int, out: int, kind: str, cached: int = 0) -> None:
    today = date.today().isoformat()
    with durable.lock():
        u = durable.read(FILE, {})
        for day in sorted(u)[:-60]:
            del u[day]
        d = u.setdefault(today, {}).setdefault(kind, {"calls": 0, "input": 0, "output": 0, "cached": 0})
        d["calls"] += 1; d["input"] += inp; d["output"] += out; d["cached"] = d.get("cached", 0) + cached
        durable.write(FILE, u)


def cost(kinds: dict) -> float:
    total, table = 0.0, prices()
    for kind, v in kinds.items():
        m = model_for(kind)
        if m is None:
            continue
        pi, pc, po = table.get(m, table.get("openai/gpt-6-luna", (0.10, 0.01, 0.50)))
        total += ((v.get("input", 0) - v.get("cached", 0)) * pi + v.get("cached", 0) * pc + v.get("output", 0) * po) / 1_000_000
    return total


def cost_today() -> float:
    return cost(durable.read(FILE, {}).get(date.today().isoformat(), {}))


def budget() -> float:
    try:
        return float(os.getenv("ELM_DAILY_DOLLAR_BUDGET") or 0.10)
    except ValueError:
        return 0.10


def over_budget() -> bool:
    return cost_today() >= budget()


def by_day(days: int = 14) -> dict:
    """{day: {kind: {calls, input, output}, "_dollars": x}} for the last days. Counts only."""
    u = durable.read(FILE, {})
    return {d: {**{k: {"calls": v.get("calls", 0), "input": v.get("input", 0), "output": v.get("output", 0)} for k, v in u[d].items()},
                "_dollars": round(cost(u[d]), 5)} for d in sorted(u)[-days:]}
