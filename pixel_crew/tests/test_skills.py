"""Тесты подбора skills и сборки промпта с SOUL-файлами."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import skills  # noqa: E402


def names(agent, ptype=None, fb=""):
    return [s["name"] for s in skills.select_skills(agent, ptype, fb)]


def test_all_skills_have_valid_front_matter():
    all_skills = skills.load_skills()
    assert len(all_skills) >= 8
    for s in all_skills:
        assert s["name"] and s["description"] and s["agents"], s
        assert (config.SKILLS_DIR / s["name"] / "SKILL.md").exists()


def test_type_specific_skill_selected():
    assert "dashboard-layout" in names("ui_generator", "dashboard")
    assert "form-design" in names("ux_researcher", "form")
    assert "dashboard-layout" not in names("ui_generator", "form")


def test_feedback_triggers_skill_first():
    got = names("ui_generator", "landing_page", "доработка: горизонтальный скролл на 375 px")
    assert got[0] == "mobile-first-layout"
    assert "apply-review-feedback" in got


def test_limits_and_agent_scope():
    for agent in ["art_director", "ux_researcher", "ui_generator", "validator"]:
        chosen = skills.select_skills(agent, "dashboard", "доработка контраст скролл")
        assert len(chosen) <= config.SKILLS_MAX
        assert sum(len(s["body"]) for s in chosen) <= config.SKILLS_BUDGET_CHARS
        assert all(agent in s["agents"] for s in chosen)


def test_system_prompt_contains_soul_role_and_skills():
    system, used = skills.build_system("validator", "form")
    assert "SOUL" in system and "Душа: Validator" in system and "TOOLS" in system
    assert "Роль: Validator" in system and used == ["design-review-rubric"]
    assert len(system) < 6000


def test_product_type_taken_from_jev_route():
    import agents
    state = {"route": {"product_type": {"value": "dashboard", "confidence": 1.0}}, "task": {}}
    assert agents._ptype(state) == "dashboard"
