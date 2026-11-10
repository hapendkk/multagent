"""Observability: спаны, инструментирование LLM/tools, метрики, алерты, траектории."""
import json

import pytest

import agent_tools
import config
import llm
import observability as obs


def test_spans_nest_and_are_written():
    t = obs.start_trace("run_test")
    with obs.span("agent.validator", "agent"):
        with obs.span("sandbox.render", "sandbox", available=True):
            pass
    inner, outer = t.spans
    assert inner["parent_id"] == outer["span_id"] and outer["parent_id"] is None
    lines = (config.TELEMETRY_DIR / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["trace_id"] == t.trace_id
    traj = obs.trajectory(t)
    assert traj[0]["name"] == "agent.validator" and traj[0]["children"][0]["kind"] == "sandbox"


def test_error_span_status():
    t = obs.start_trace("r")
    with pytest.raises(ValueError):
        with obs.span("x", "tool"):
            raise ValueError("boom")
    assert t.spans[0]["status"] == "error"


def test_llm_and_tool_calls_instrumented(monkeypatch):
    t = obs.start_trace("r")
    monkeypatch.setattr(llm, "_post", lambda p, pl: {"message": {"content": "ok"},
                                                    "prompt_eval_count": 120, "eval_count": 30})
    llm.chat("qwen3.5:4b", "s", "u")
    agent_tools.execute("check_contrast", {"fg": "#000000", "bg": "#ffffff"}, "ui_generator")
    llm_span = next(s for s in t.spans if s["kind"] == "llm")
    assert llm_span["attributes"]["tokens_in"] == 120 and llm_span["attributes"]["tokens_out"] == 30
    assert any(s["name"] == "tool.check_contrast" for s in t.spans)
    m = obs.finish({"outcome": "pass", "attempt": 1})["metrics"]
    assert m["llm_calls"] == 1 and m["tool_calls"] == 1 and m["by_model"]["qwen3.5:4b"]["tokens_out"] == 30


def test_alert_rules():
    base = {"run_id": "r", "trace_id": "t", "run_seconds": 500, "llm_errors": 1, "sandbox_available": False,
            "outcome": "fail", "attempts": 3,
            "by_model": {"qwen3.5:4b": {"tokens_per_s": 3.0, "tokens_out": 600}}}
    rules = {a["rule"] for a in obs.check_alerts(base)}
    assert rules == {"slow_run", "llm_errors", "sandbox_down", "run_failed", "slow_model"}
    history = [{"outcome": "fail"}] * 6
    assert "pass_rate_drop" in {a["rule"] for a in obs.check_alerts(base, history)}
    ok = dict(base, run_seconds=100, llm_errors=0, sandbox_available=True, outcome="pass", by_model={})
    assert obs.check_alerts(ok, [{"outcome": "pass"}] * 6) == []


def test_disabled_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "TELEMETRY_ENABLED", False)
    obs.start_trace("r")
    with obs.span("x"):
        pass
    assert not config.TELEMETRY_DIR.exists()


def test_graph_nodes_produce_agent_spans(monkeypatch):
    import test_graph_mock
    t = obs.start_trace("graph")
    test_graph_mock.test_cascade_light_then_strong(monkeypatch)
    names = [s["name"] for s in t.spans if s["kind"] == "agent"]
    assert {"agent.art_director", "agent.ux_researcher", "agent.ui_generator", "agent.validator"} <= set(names)
    assert names.count("agent.ui_generator") == 2          # каскад: light FAIL -> strong
    m = obs.finish({"outcome": "pass", "attempt": 2})
    assert [n["name"] for n in m["trajectory"]][0] == "agent.art_director"
