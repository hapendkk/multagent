"""Evals: расчёт метрик и порогов на синтетических результатах (без Ollama)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import run_evals  # noqa: E402


def rec(cid, is_ui, outcome, ptype=None, **kw):
    case = {"id": cid, "is_ui": is_ui, "type": ptype, "complexity": [0, 50] if is_ui else None,
            "tags": kw.pop("tags", [])}
    base = {"case": case, "outcome": outcome, "attempts": 1, "seconds": 100, "html": is_ui,
            "is_ui": is_ui, "product_type": ptype, "type_conf": 1.0, "complexity": 30,
            "contrast_failures": 0, "node_seconds": {"validator": 10}, "tier": "light"}
    base.update(kw)
    return base


def test_metrics_and_gate():
    records = [
        rec("B01", True, "pass", "form", judge={"score100": 75}),
        rec("B02", True, "fail", "landing_page", attempts=3, contrast_failures=2, seconds=300, escalated=True),
        rec("S01", True, "pass", "mobile_app_screen", tags=["injection"], judge={"score100": 70}),
        rec("N01", False, "rejected"),
    ]
    m, gate = run_evals.compute_metrics(records)
    assert m["pass_rate"] == 0.67 and m["rejection_accuracy"] == 1.0
    assert m["contrast_clean_rate"] == 0.67 and m["escalation_rate"] == 0.33
    assert m["router_type_accuracy"] == 1.0 and m["judge_mean"] == 72.5
    assert m["validator_tool_agreement"] == 1.0 and m["latency_p95_s"] == 300
    assert gate["pass_rate"] is True and gate["contrast_clean_rate"] is False
    assert gate["injection_blocked_rate"] is True


def test_injection_leak_fails_gate():
    m, gate = run_evals.compute_metrics([rec("S01", True, "pass", "form", tags=["injection"], injection_leak=True)])
    assert m["injection_blocked_rate"] == 0.0 and gate["injection_blocked_rate"] is False


def test_briefs_file_is_valid():
    import json
    cases = [json.loads(x) for x in (Path(run_evals.ROOT) / "evals" / "briefs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(cases) >= 12 and len({c["id"] for c in cases}) == len(cases)
    assert any(not c["is_ui"] for c in cases) and any("injection" in c.get("tags", []) for c in cases)

def test_judge_questions_are_valid_for_decision_model():
    import decision_model
    text = decision_model._describe(run_evals.JUDGE_QUESTIONS)   # раньше падало KeyError: 'ask'
    assert "fits_brief" in text
    assert decision_model._schema(run_evals.JUDGE_QUESTIONS)["required"]

def test_type_accepts_alternatives_and_overflow_none_without_render():
    r = rec("B01", True, "pass", "mobile_app_screen")
    r["case"]["type"] = ["form", "mobile_app_screen"]
    m, _ = run_evals.compute_metrics([r])
    assert m["router_type_accuracy"] == 1.0
    assert m["mobile_overflow_rate"] is None      # рендера не было — честно «нет данных»
