"""Память: эпизоды, уроки с затуханием, кэш роутера, правила актуализации."""
import config
import memory

DAY = 86400
T0 = 1_800_000_000


def state(outcome="pass", ptype="dashboard", brief="Дашборд продаж кофейни"):
    return {"brief": brief, "route": {"product_type": {"value": ptype}, "complexity": {"value": 70}},
            "generator_model": "qwen3.5:4b", "outcome": outcome, "attempt": 2,
            "verdict": {"quality": 80}, "task": {"product_name": "Brew"}}


def test_categorize():
    assert memory.categorize("контраст 3.15:1 у button") == "contrast"
    assert memory.categorize("горизонтальный скролл на 375 px") == "overflow"
    assert memory.categorize("что-то непонятное") is None


def test_lessons_accumulate_and_feed_prompt():
    for i in range(3):
        memory.record_run(state(), ["- контраст 2.3:1 у header\n- горизонтальный скролл", ""], now=T0 + i)
    lessons = memory.get_lessons("dashboard", now=T0 + 10)
    assert {x["category"] for x in lessons} == {"contrast", "overflow"}
    assert memory.get_lessons("form", now=T0 + 10) == []


def test_decay_and_prune():
    memory.record_run(state(), ["- контраст 2.3:1"], now=T0)
    assert memory.get_lessons("dashboard", now=T0 + 30 * DAY) == []     # 1 × 0.5^(30/14) < 0.5
    memory.record_run(state(brief="другое"), [""], now=T0 + config.LESSON_TTL_DAYS * DAY + DAY)
    with memory._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 0


def test_similar_runs_and_rejected_not_stored():
    memory.record_run(state(outcome="fail"), ["- контраст"], now=T0)
    memory.record_run(state(outcome="rejected", brief="рецепт борща"), [], now=T0)
    sim = memory.similar_runs("Дашборд продаж для кофейни", "dashboard")
    assert sim and sim[0]["outcome"] == "fail" and sim[0]["issues"] == ["contrast"]
    assert memory.similar_runs("рецепт борща") == []


def test_router_cache_ttl():
    memory.cache_put("router", "Экран входа", {"x": 1}, now=T0)
    assert memory.cache_get("router", "  экран   входа ", now=T0 + 3600) == {"x": 1}
    assert memory.cache_get("router", "Экран входа", now=T0 + 8 * DAY) is None


def test_memory_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_ENABLED", False)
    assert memory.record_run(state(), ["- контраст"]) == {}
    assert memory.get_lessons("dashboard") == []

def test_record_run_accepts_real_verdict_shape():
    st = state()
    st["verdict"] = {"quality": {"value": 75, "confidence": 0.9}}
    memory.record_run(st, [""], now=T0)
    with memory._conn() as c:
        assert c.execute("SELECT quality FROM runs").fetchone()[0] == 75