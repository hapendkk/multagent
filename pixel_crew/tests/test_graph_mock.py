"""Проход всего графа LangGraph с подменёнными LLM (без Ollama):
простая задача -> лёгкая модель -> FAIL -> эскалация на сильную -> PASS,
валидатор каждый раз из другого семейства."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agents  # noqa: E402
import config  # noqa: E402
import router  # noqa: E402
from graph import build_graph  # noqa: E402

GOOD = ("<!doctype html><html lang='ru'><head><meta name='viewport' content='width=device-width'>"
        "<title>Sereni</title><style>body{font-family:system-ui;color:#111;background:#fff;"
        "max-width:480px;margin:0 auto}@media(min-width:600px){}</style></head>"
        "<body><h1>Sereni</h1></body></html>")
BAD = "<html><body>черновик</body></html>"


def run(monkeypatch, complexity, product_type="mobile_app_screen"):
    calls = {"decide": [], "chat": []}

    def fake_decide(model, role, context, questions, n_votes):
        calls["decide"].append(model)
        ans = {"is_ui_request": True, "product_type": product_type, "complexity": complexity,
               "matches_brief": True, "quality": 80}
        out = {k: {"value": ans[k], "confidence": 1.0} for k in questions}
        out["_meta"] = {"model": model, "votes_ok": n_votes, "votes_requested": n_votes,
                        "seconds": 0, "problems": [], "mode": "schema"}
        return out

    def fake_chat(model, system, user, **kw):
        calls["chat"].append(model)
        if "schema" in kw:
            text = '{"product_name": "Sereni", "audience": "25-35", "tone": "спокойный", ' \
                   '"main_screen": "выбор", "key_elements": ["кнопка"]}'
        elif "HTML" in user:
            text = GOOD if model == config.GENERATOR_STRONG else BAD
        else:
            text = "заметки"
        return {"text": text, "seconds": 0}

    monkeypatch.setattr(agents, "decide", fake_decide)
    monkeypatch.setattr(agents.llm, "chat", fake_chat)
    monkeypatch.setattr(agents.llm, "chat_tools", lambda *a, **k: {
        "text": '{"bg": "#ffffff", "text": "#111111"}', "calls": [], "rounds": 1, "seconds": 0})
    monkeypatch.setattr(agents.tools, "render_screenshot", lambda html: {"available": False, "reason": "тест"})
    state = {"brief": "Экран медитации", "task": {}, "route": {}, "generator_model": "", "ux_notes": "",
             "html": "", "feedback": "", "verdict": {}, "attempt": 0, "outcome": "running", "log": []}
    return build_graph().invoke(state), calls


def test_cascade_light_then_strong(monkeypatch):
    final, calls = run(monkeypatch, complexity=20)
    assert final["outcome"] == "pass"
    assert final["attempt"] == 2
    assert final["route"]["generator_tier"] == "light"
    assert final["generator_model"] == config.GENERATOR_STRONG
    assert any("Эскалация" in line for line in final["log"])
    validators = calls["decide"][1:]
    assert validators == [router.pick_validator(config.GENERATOR_LIGHT),
                          router.pick_validator(config.GENERATOR_STRONG)]

def test_complex_task_strong_from_start(monkeypatch):
    final, _ = run(monkeypatch, complexity=80)
    assert final["outcome"] == "pass" and final["attempt"] == 1
    assert final["verdict"]["validator_model"] == config.VALIDATOR_MODEL

def test_retry_after_truncation_asks_for_compact(monkeypatch):
    seen = {}

    def fake_chat(model, system, user, **kw):
        seen["user"], seen["max_tokens"] = user, kw.get("max_tokens")
        return {"text": GOOD, "seconds": 0, "done_reason": "stop"}

    monkeypatch.setattr(agents.llm, "chat", fake_chat)
    monkeypatch.setattr(agents.llm, "chat_tools",
                        lambda *a, **k: {"text": "{}", "calls": [], "rounds": 1, "seconds": 0})
    state = {"brief": "дашборд", "task": {"product_name": "X"},
             "route": {"product_type": {"value": "dashboard"}},
             "generator_model": config.GENERATOR_STRONG, "ux_notes": "",
             "html": "<html><body>обрезано", "feedback": "- Линтер: HTML не закрыт",
             "attempt": 1, "log": []}
    agents.ui_generator(state)
    assert "ОБРЕЗАНА" in seen["user"] and "обрезано" not in seen["user"]
    assert seen["max_tokens"] == config.GEN_MAX_TOKENS["strong"]
