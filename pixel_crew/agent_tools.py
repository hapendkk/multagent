"""Инструменты, которые агент вызывает САМ через tool calling (Ollama `tools`).

Два класса инструментов в Pixel Crew:
1. Обязательные проверки (контраст всей страницы, линтер, рендер в песочнице)
   вызывает КОД валидатора: шлюз качества не должен зависеть от того,
   решит ли модель вызвать инструмент.
2. Вспомогательные инструменты (этот модуль) агент вызывает по своему решению:
   проверить пару цветов, прочитать полный текст навыка.

Безопасность: у каждого агента свой allowlist, аргументы валидируются,
ошибки возвращаются модели текстом и не роняют граф, число раундов ограничено.
"""
import re

import observability as obs
import skills
import tools

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

SPECS = {
    "check_contrast": {
        "type": "function",
        "function": {
            "name": "check_contrast",
            "description": "Точно считает контраст WCAG 2.x между цветом текста и фона "
                           "и предлагает ближайшее исправление, если пара не проходит AA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fg": {"type": "string", "description": "цвет текста, HEX, например #1a1a1a"},
                    "bg": {"type": "string", "description": "цвет фона, HEX, например #ffffff"},
                    "large_text": {"type": "boolean", "description": "крупный текст (≥ 24px или ≥ 19px жирный)"},
                },
                "required": ["fg", "bg"],
            },
        },
    },
    "read_skill": {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": "Возвращает полный текст навыка дизайн-студии по имени.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "имя навыка"}},
                "required": ["name"],
            },
        },
    },
}

AGENT_TOOLS = {
    "ui_generator": ["check_contrast"],
    "ux_researcher": ["read_skill"],
}


def specs(agent):
    out = []
    for name in AGENT_TOOLS.get(agent, []):
        spec = SPECS[name]
        if name == "read_skill":
            names = [s["name"] for s in skills.load_skills() if agent in s.get("agents", [])]
            spec = {**spec, "function": {**spec["function"], "description":
                    spec["function"]["description"] + " Доступные: " + ", ".join(names)}}
        out.append(spec)
    return out


def _check_contrast(fg, bg, large_text=False):
    for v in (fg, bg):
        if not isinstance(v, str) or not _HEX.match(v.strip()):
            return {"error": f"ожидается HEX-цвет вида #1a2b3c, получено {v!r}"}
    f, b = tools.hex_to_rgb(fg.strip()), tools.hex_to_rgb(bg.strip())
    target = 3.0 if large_text else tools.AA_NORMAL
    ratio = round(tools.contrast_ratio(f, b), 2)
    res = {"fg": fg, "bg": bg, "ratio": ratio, "required": target, "passes_aa": ratio >= target}
    if ratio < target:
        res["suggestion"] = tools.suggest_fix(f, b, target)
    return res


def _read_skill(name, agent):
    for s in skills.load_skills():
        if s["name"] == name and agent in s.get("agents", []):
            return {"name": name, "text": s["body"]}
    return {"error": f"навык {name!r} не найден или недоступен агенту {agent}"}


def execute(name, args, agent):
    """Выполняет вызов инструмента. Никогда не бросает исключение."""
    with obs.span(f"tool.{name}", "tool", agent=agent, args=args) as a:
        res = _execute(name, args, agent)
        a["error"] = res.get("error")
        return res


def _execute(name, args, agent):
    if name not in AGENT_TOOLS.get(agent, []):
        return {"error": f"инструмент {name!r} не разрешён агенту {agent}"}
    if not isinstance(args, dict):
        return {"error": "аргументы должны быть объектом JSON"}
    try:
        if name == "check_contrast":
            return _check_contrast(args.get("fg"), args.get("bg"), bool(args.get("large_text")))
        if name == "read_skill":
            return _read_skill(args.get("name"), agent)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": f"нет реализации {name}"}
