"""The real pydantic-ai path of the chat and the interview, offline: a FunctionModel stands in for the network model, so
Agent construction, structured output, output types, message history and usage reading all run as in production.
Why: 2026-11-05, every real answer turned into "busy" because `res.usage()` became a property in this pydantic-ai
version; the other tests replaced `_run` whole and never saw it. Prints "elm agent path: ALL OK"."""
import asyncio, json, os, shutil, sys, tempfile
os.environ["ELM_SNAPSHOT_URL"] = "off"   # offline: never fetch the published data snapshot
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(ROOT / "elm"), str(ROOT / "backend")]
tmp = Path(tempfile.mkdtemp())
shutil.copy(HERE / "fixture_roles.json", tmp / "roles.json")
os.environ.update(ELM_DATA_DIR=str(tmp), ELM_CHAT_MODEL="fake/model", OPENROUTER_API_KEY="sk-or-test-not-real")
os.environ.pop("ELM_DATABASE_URL", None)

from pydantic_ai.models.function import FunctionModel, AgentInfo  # noqa: E402
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart  # noqa: E402
import chat, interview  # noqa: E402

ROLES = json.loads((tmp / "roles.json").read_text())["roles"]
seen_prompts = []


def parse_model(messages, info: AgentInfo):
    """The filters step: a PromptedOutput agent answers with JSON text (or a tool call on models that prefer it)."""
    seen_prompts.append(str(messages[-1].parts[-1].content))
    body = {"kind": "intern", "chips": ["marketing", "not-a-chip"], "keywords": ["analyst"], "places": ["Boston"], "company": "Nowhere Inc"}
    out_tool = info.output_tools[0].name if info.output_tools else None
    return ModelResponse(parts=[ToolCallPart(out_tool, body)] if out_tool else [TextPart(json.dumps(body))])


def profile_model(messages, info: AgentInfo):
    prof = {"kind": "intern", "chips": ["data", "not-a-chip"], "exclude_chips": ["hw"], "keywords": ["museum"], "avoid": [], "locations": ["Boston"]}
    out_tool = info.output_tools[0].name if info.output_tools else None
    return ModelResponse(parts=[ToolCallPart(out_tool, prof)] if out_tool else [TextPart(json.dumps(prof))])


chat._build_model = lambda name: FunctionModel(parse_model)
chat._agent = None
r = asyncio.run(chat.answer("something with numbers please", [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]))
assert seen_prompts and "something with numbers" in seen_prompts[0], seen_prompts      # the real Agent ran, with the message as its prompt
f = r["filters"]
assert f["kind"] == "intern" and f["chips"] == ["marketing"] and f["keywords"] == ["analyst"] and f["places"] == ["Boston"] and f["company"] == "", f   # validated in code
assert r["reply"].startswith("Searched: internships") and "Nowhere" not in r["reply"] and "busy" not in r, r
assert [x["id"] for x in r["roles"]] == ["x4"]                  # cards are our own records
use = json.loads((tmp / "_usage.json").read_text())
assert any("elm_chat" in v for v in use.values()), use                               # usage was read and metered

interview._agents.clear()
chat._build_model = lambda name: FunctionModel(profile_model)
h = [{"role": "assistant", "text": "Internship or full-time?"}, {"role": "user", "text": "internship"},
     {"role": "assistant", "text": "Field?"}, {"role": "user", "text": "data"}, {"role": "assistant", "text": "Where?"},
     {"role": "user", "text": "Boston"}]
s = asyncio.run(interview.step(h, "en", [{"id": "stats"}]))
assert "profile" in s and s.get("source") != "code", s
assert "not-a-chip" not in s["profile"]["chips"] and "data" in json.dumps(s["profile"]), s
assert any("elm_profile" in v for v in json.loads((tmp / "_usage.json").read_text()).values())   # the paid-model step is metered as elm_profile
print("elm agent path: ALL OK")
shutil.rmtree(tmp, ignore_errors=True)
