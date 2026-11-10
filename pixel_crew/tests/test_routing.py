"""Юнит-тесты роутинга и проверки названия (без Ollama).   python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import router  # noqa: E402
import tools  # noqa: E402


def test_simple_task_goes_to_light_model():
    model, tier, _ = router.route_generator(20, "mobile_app_screen")
    assert (model, tier) == (config.GENERATOR_LIGHT, "light")


def test_complex_task_goes_to_strong_model():
    assert router.route_generator(70, "landing_page")[1] == "strong"


def test_dashboard_always_strong_and_unknown_complexity_is_strong():
    assert router.route_generator(10, "dashboard")[1] == "strong"
    assert router.route_generator(None, "form")[1] == "strong"


def test_escalation_only_on_retry_from_light():
    assert router.escalate(config.GENERATOR_LIGHT, 1) == (config.GENERATOR_LIGHT, False)
    assert router.escalate(config.GENERATOR_LIGHT, 2) == (config.GENERATOR_STRONG, True)
    assert router.escalate(config.GENERATOR_STRONG, 2) == (config.GENERATOR_STRONG, False)


def test_validator_is_always_other_family():
    for gen in ["qwen3.5:4b", "llama3.2:latest", "gemma4:e2b", "phi4-mini:latest"]:
        val = router.pick_validator(gen)
        assert router.family(val) != router.family(gen), (gen, val)
    assert router.pick_validator("gemma4:e2b") == "phi4-mini:latest"


def test_mixed_script_name_is_fixed():
    assert tools.fix_mixed_script("\u0421ereni") == "Cereni"          # кириллическая С в латинском слове
    assert tools.fix_mixed_script("Дзeн") == "Дзен"                    # латинская e в русском слове
    assert tools.fix_mixed_script("Calm Space") == "Calm Space"


def test_name_presence():
    html_text = "Sereni — медитация. Sereni © 2026"
    assert tools.name_presence("Sereni", html_text) == "exact"
    assert tools.name_presence("\u0421ereni", html_text) == "similar"   # баг из запуска 14:44
    assert tools.name_presence("Дзен Тайм", "Приложение дзен тайм") == "exact"
    assert tools.name_presence("Focusly", html_text) == "missing"


def test_bug_1444_is_warning_not_error():
    html = ("<!doctype html><html lang='ru'><head><meta name='viewport' content='width=device-width'>"
            "<title>Sereni</title><style>body{font-family:system-ui;max-width:480px;margin:0 auto}"
            "@media(min-width:600px){}</style></head><body><h1>Sereni</h1></body></html>")
    lint = tools.lint_html(html, "\u0421ereni")
    assert not any("название" in e for e in lint["errors"])
    assert any("название" in w for w in lint["warnings"])
