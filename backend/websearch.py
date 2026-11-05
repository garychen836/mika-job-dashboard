"""Searching the web for job postings, with the model's own search tool.

One call to the Responses API (the chat API of the same model has no search): the model searches at most twice and
replies with links only. The links are only candidates. Every one is opened by code (postings.read), so a link the
model made up simply fails to read, and nothing it says about a posting is used.
"""

import os
import re

from openai import AsyncOpenAI


INSTRUCTIONS = ("Find links to individual, currently open job postings that match the request. Search at most twice. Give "
                "only direct links to a single posting (a company careers page or a recruiting-system page), never a search "
                "or list page, never a news article. Reply with one link per line and nothing else; at most 8 links.")


def _client() -> AsyncOpenAI:
    import agent
    return agent.client(timeout=120, retries=0)


EVENTS = ("Find links to individual upcoming event pages on Luma (luma.com or lu.ma) that match the request, in the city and "
          "dates given. Search at most twice. Only links to a single event page, never a calendar, search or list page. Reply "
          "with one link per line and nothing else; at most 15 links.")


async def links(request: str, limit: int = 8, instructions: str = INSTRUCTIONS) -> tuple[list[str], dict]:
    """(candidate links, tokens used). The request is the student's words, cut to a sentence or two."""
    import agent
    r = await _client().responses.create(
        model=agent.model_id(agent.SEARCH_MODEL or agent.MODEL_NAME, search=True), instructions=instructions, input=request[:400],
        tools=[agent.search_tool()], max_output_tokens=1500, reasoning={"effort": "low"})
    found = re.findall(r"https://[^\s<>\")\]]+", r.output_text or "")
    for o in r.output:                                                   # cited pages too, when the model gives them
        if o.type == "message":
            for part in o.content:
                found += [a.url for a in (getattr(part, "annotations", None) or []) if getattr(a, "url", None)]
    clean = list(dict.fromkeys(re.sub(r"[?&]utm_[^&]*", "", u.rstrip(".,;")) for u in found))
    return clean[:limit], {"input": r.usage.input_tokens, "output": r.usage.output_tokens,
                           "cached": getattr(getattr(r.usage, "input_tokens_details", None), "cached_tokens", 0) or 0}


# ---------- what students look for, as tags: never their words ----------
TYPES = {"summer": "summer internship", "intern": "summer internship", "in-semester": "in-semester internship",
         "full-time": "full-time", "full time": "full-time", "fellowship": "fellowship"}
CONDITIONS = {"sponsor": "needs sponsorship", "visa": "needs sponsorship", "h-1b": "needs sponsorship", "remote": "remote",
              "new york": "region: New York", "nyc": "region: New York", "boston": "region: Boston", "bay area": "region: SF Bay Area",
              "san francisco": "region: SF Bay Area", "silicon valley": "region: SF Bay Area", "london": "region: London",
              "europe": "region: Europe", "asia": "region: Asia", "china": "region: Asia", "singapore": "region: Asia"}
