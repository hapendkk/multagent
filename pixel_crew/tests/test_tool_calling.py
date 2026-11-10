"""Tool calling: инструменты агента, агентный цикл и страховка палитры (без Ollama)."""
import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_tools  # noqa: E402
import agents  # noqa: E402
import llm  # noqa: E402


def test_check_contrast_tool_is_exact():
    r = agent_tools.execute("check_contrast", {"fg": "#777777", "bg": "#ffffff"}, "ui_generator")
    assert r["ratio"] == 4.48 and r["passes_aa"] is False and r["suggestion"]   # T11 из бенчмарка
    assert agent_tools.execute("check_contrast", {"fg": "#777", "bg": "#fff", "large_text": True},
                               "ui_generator")["passes_aa"] is True


def test_allowlist_and_validation():
    assert "error" in agent_tools.execute("check_contrast", {"fg": "#000", "bg": "#fff"}, "validator")
    assert "error" in agent_tools.execute("check_contrast", {"fg": "red", "bg": "#fff"}, "ui_generator")
    assert "error" in agent_tools.execute("rm_rf", {}, "ui_generator")
    assert agent_tools.execute("read_skill", {"name": "form-design"}, "ux_researcher")["text"]
    assert "error" in agent_tools.execute("read_skill", {"name": "design-review-rubric"}, "ux_researcher")
    assert "error" in agent_tools.execute("read_skill", {"name": "form-design"}, "ui_generator")


def test_agent_loop_executes_tool_calls(monkeypatch):
    sent = []
    replies = iter([
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "check_contrast", "arguments": {"fg": "#ffffff", "bg": "#3498db"}}}]}},
        {"message": {"content": '{"accent": "#1f5f5b"}'}},
    ])

    def fake_post(path, payload):
        sent.append(json.loads(json.dumps(payload)))
        return next(replies)

    monkeypatch.setattr(llm, "_post", fake_post)
    r = llm.chat_tools("qwen3.5:4b", "sys", "user", agent_tools.specs("ui_generator"),
                       lambda n, a: agent_tools.execute(n, a, "ui_generator"))
    assert r["rounds"] == 2 and r["text"] == '{"accent": "#1f5f5b"}'
    assert r["calls"][0]["result"]["passes_aa"] is False
    tool_msg = sent[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_name"] == "check_contrast"
    assert "tools" in sent[0]


def test_round_limit(monkeypatch):
    call = {"message": {"content": "", "tool_calls": [{"function": {
        "name": "check_contrast", "arguments": {"fg": "#000000", "bg": "#ffffff"}}}]}}
    monkeypatch.setattr(llm, "_post", lambda path, payload: call if "tools" in payload
                        else {"message": {"content": "{}"}})
    r = llm.chat_tools("m", "s", "u", agent_tools.specs("ui_generator"),
                       lambda n, a: agent_tools.execute(n, a, "ui_generator"), max_rounds=3)
    assert r["rounds"] == 3 and len(r["calls"]) == 2


def test_fallback_when_model_has_no_tools(monkeypatch):
    def fake_post(path, payload):
        if "tools" in payload:
            raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":"model does not support tools"}'))
        return {"message": {"content": "ok"}}
    monkeypatch.setattr(llm, "_post", fake_post)
    r = llm.chat_tools("gemma3", "s", "u", agent_tools.specs("ui_generator"), lambda n, a: {})
    assert r["tools_supported"] is False and r["text"] == "ok"


def test_enforce_palette_fixes_model_mistakes():
    bad = dict(agents.DEFAULT_PALETTE, accent="#3498db", accent_hover="#5dade2", muted="#aaaaaa")
    fixed, fixes = agents.enforce_palette(bad)
    assert len(fixes) >= 3
    for fg, bg, _ in agents.PALETTE_PAIRS:
        from tools import contrast_ratio, hex_to_rgb
        assert contrast_ratio(hex_to_rgb(fixed[fg]), hex_to_rgb(fixed[bg])) >= 4.5


def test_plan_palette_survives_ollama_down(monkeypatch):
    def down(*a, **k):
        raise llm.LLMError("нет связи")
    monkeypatch.setattr(llm, "chat_tools", down)
    palette, info = agents.plan_palette("qwen3.5:4b", {"product_name": "X"})
    assert palette == agents.DEFAULT_PALETTE and info["error"]
