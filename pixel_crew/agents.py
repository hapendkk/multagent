"""Четыре агента Pixel Crew. Каждый агент это узел графа: получает общее
состояние и возвращает только те поля, которые изменил."""
import re

import agent_tools
import config
import llm
import memory
import router
import skills
import tools
from decision_model import decide
from state import PixelState


def load_prompt(name):
    return (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _ptype(state):
    pt = state["route"].get("product_type")
    return pt.get("value") if isinstance(pt, dict) else pt


def _short(model):
    return model.split(":")[0]


# ---------------------------------------------------------------------------
# 1. Art Director: оркестратор + Jev-like роутинг
# ---------------------------------------------------------------------------

PRODUCT_TYPES = ["landing_page", "mobile_app_screen", "dashboard", "form", "other"]

ROUTER_QUESTIONS = {
    "is_ui_request": {"type": "bool",
                      "ask": "Это запрос на дизайн интерфейса (экран, страница, приложение)?"},
    "product_type": {"type": "choice", "options": PRODUCT_TYPES,
                     "ask": "Какой тип интерфейса просят?"},
    "complexity": {"type": "score", "min": 0, "max": 100,
                   "ask": "Насколько сложен интерфейс? 0 = одна простая карточка, "
                          "100 = многосекционный сложный дашборд."},
}

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "product_name": {"type": "string"},
        "audience": {"type": "string"},
        "tone": {"type": "string"},
        "main_screen": {"type": "string"},
        "key_elements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["product_name", "audience", "tone", "main_screen", "key_elements"],
}


def art_director(state: PixelState):
    brief = state["brief"]
    d = memory.cache_get("router", brief)
    if d:
        d["_meta"]["seconds"], d["_meta"]["cached"] = 0, True
    else:
        d = decide(
            model=config.ROUTER_MODEL,
            role="Ты Jev-like модель дизайн-студии: классифицируешь запросы клиентов.",
            context=f"Бриф клиента:\n{brief}",
            questions=ROUTER_QUESTIONS,
            n_votes=config.ROUTER_VOTES,
        )
        if all(d[k]["confidence"] >= config.CONF_THRESHOLD for k in ROUTER_QUESTIONS):
            memory.cache_put("router", brief, d)
    meta = d["_meta"]
    route = {k: d[k] for k in ROUTER_QUESTIONS}
    log = [f"[Art Director] Jev-like решения ({_short(meta['model'])}{', из кэша памяти' if meta.get('cached') else ''}, "
           f"{meta['votes_ok']}/{meta['votes_requested']} голосов, {meta['seconds']} с): "
           f"UI-запрос={d['is_ui_request']['value']} (увер. {d['is_ui_request']['confidence']}), "
           f"тип={d['product_type']['value']} (увер. {d['product_type']['confidence']}), "
           f"сложность={d['complexity']['value']} (увер. {d['complexity']['confidence']})"]

    if meta["votes_ok"] == 0:
        log.append("[Art Director] ВНИМАНИЕ: ни один голос не получен: " + " | ".join(meta["problems"]))

    # Шлюз 1: входной фильтр
    is_ui = d["is_ui_request"]
    if is_ui["value"] is False and is_ui["confidence"] >= config.CONF_THRESHOLD:
        log.append("[Art Director] Бриф не похож на запрос интерфейса, работа остановлена.")
        return {"outcome": "rejected", "route": route, "log": log}

    # Роутинг: простая задача на лёгкую модель, сложная на сильную
    complexity = d["complexity"]["value"]
    generator, tier, reason = router.route_generator(complexity, d["product_type"]["value"])
    route.update({"generator_tier": tier, "generator_reason": reason})

    # Планирование: структурированное задание команде
    r = llm.chat(
        config.ROUTER_MODEL, skills.build_system("art_director", d["product_type"]["value"])[0],
        f"Бриф клиента:\n{brief}\n\nТип интерфейса: {d['product_type']['value']}. "
        "Составь задание для команды.",
        schema=TASK_SCHEMA, temperature=0.3, max_tokens=500)
    task = llm.parse_json(r["text"]) or {}
    if not (task.get("product_name") or "").strip():
        # маленькие модели часто оставляют имя пустым: спрашиваем отдельно
        try:
            n = llm.chat(config.ROUTER_MODEL, "Ты придумываешь короткие названия приложений.",
                         f"Бриф:\n{brief}\n\nПридумай название приложения из 1-3 слов. "
                         "Ответь только названием, без кавычек и пояснений.",
                         temperature=0.8, max_tokens=15)
            lines = n["text"].strip().splitlines()
            task["product_name"] = lines[0].strip(" «»\"'.") if lines else ""
        except llm.LLMError:
            task["product_name"] = ""
    task["product_name"] = tools.fix_mixed_script(task.get("product_name") or "")
    defaults = {"product_name": "Проект", "audience": "широкая аудитория",
                "tone": "нейтральный", "main_screen": brief[:80], "key_elements": []}
    for key, value in defaults.items():
        if not task.get(key):
            task[key] = value

    log.append(f"[Art Director] Задание: «{task['product_name']}», аудитория: {task['audience']}. "
               f"Роутинг: {reason} -> {tier}, UX и код на {generator} ({r['seconds']} с)")
    return {"task": task, "route": route, "generator_model": generator,
            "outcome": "running", "log": log}


# ---------------------------------------------------------------------------
# 2. UX Researcher
# ---------------------------------------------------------------------------

def ux_researcher(state: PixelState):
    model = state["generator_model"]
    task = state["task"]
    user = (f"Бриф клиента:\n{state['brief']}\n\n"
            f"Задание: {task}\n\nПодготовь материалы по инструкции.")
    past = memory.similar_runs(state["brief"], _ptype(state))
    if past:
        user += "\n\nПохожие прошлые задания студии (учти их проблемы):\n" + "\n".join(
            f"- «{p['brief']}»: итог {p['outcome']}, попыток {p['attempts']}, "
            f"проблемы: {', '.join(p['issues']) or 'нет'}" for p in past)
    system, used = skills.build_system("ux_researcher", _ptype(state))
    r = llm.chat(model, system, user, temperature=0.4, max_tokens=600, num_ctx=4096)
    return {"ux_notes": r["text"],
            "log": [f"[UX Researcher] Заметки готовы ({_short(model)}, {r['seconds']} с), "
                    f"skills: {', '.join(used) or 'нет'}"]}


# ---------------------------------------------------------------------------
# 3. UI Generator
# ---------------------------------------------------------------------------

def extract_html(text):
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S | re.I)
    code = m.group(1) if m else text
    low = code.lower()
    i = low.find("<!doctype")
    if i == -1:
        i = low.find("<html")
    if i != -1:
        code = code[i:]
    j = code.lower().rfind("</html>")
    if j != -1:
        code = code[:j + 7]
    return code.strip()


PALETTE_KEYS = ["bg", "surface", "text", "muted", "accent", "accent_hover", "on_accent"]
DEFAULT_PALETTE = {"bg": "#f7f5f2", "surface": "#ffffff", "text": "#1a1a1a", "muted": "#595959",
                   "accent": "#1f5f5b", "accent_hover": "#174845", "on_accent": "#ffffff"}
# (текст, фон, что менять при провале)
PALETTE_PAIRS = [("text", "bg", "fg"), ("muted", "bg", "fg"), ("text", "surface", "fg"),
                 ("muted", "surface", "fg"), ("on_accent", "accent", "bg"),
                 ("on_accent", "accent_hover", "bg")]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def enforce_palette(palette):
    """Гарантия кодом: все пары палитры проходят AA, даже если модель ошиблась."""
    p, fixes = dict(palette), []
    for fg, bg, side in PALETTE_PAIRS:
        f, b = tools.hex_to_rgb(p[fg]), tools.hex_to_rgb(p[bg])
        ratio = tools.contrast_ratio(f, b)
        if ratio >= tools.AA_NORMAL:
            continue
        sug = tools.suggest_fix(f, b)
        key = "new_bg" if side == "bg" else "new_fg"
        alt = "new_fg" if side == "bg" else "new_bg"
        if key in sug:
            target = bg if side == "bg" else fg
            p[target] = sug[key]
        elif alt in sug:
            target = fg if side == "bg" else bg
            p[target] = sug[alt]
        else:
            continue
        fixes.append(f"{fg}/{bg}: {ratio:.2f}:1 -> {target}={p[target]}")
    return p, fixes


PALETTE_SYSTEM = (
    "Ты дизайнер цвета студии Pixel Crew. Подбери палитру токенов под задание.\n"
    "Обязательно проверь инструментом check_contrast пары: text/bg, muted/bg, text/surface, "
    "on_accent/accent, on_accent/accent_hover. Если пара не проходит, исправь цвет и проверь снова.\n"
    "Финальный ответ: только JSON-объект с ключами " + ", ".join(PALETTE_KEYS) + " (значения HEX #rrggbb)."
)


def plan_palette(model, task, feedback=""):
    """Шаг tool calling: модель сама проверяет цвета инструментом, код страхует результат."""
    user = f"Задание: {task}\nЗамечания проверяющего: {feedback or 'нет'}"
    try:
        r = llm.chat_tools(model, PALETTE_SYSTEM, user, agent_tools.specs("ui_generator"),
                           lambda n, a: agent_tools.execute(n, a, "ui_generator"),
                           max_rounds=config.TOOL_MAX_ROUNDS, temperature=0.3,
                           max_tokens=400, num_ctx=4096)
    except llm.LLMError as e:
        return DEFAULT_PALETTE.copy(), {"calls": [], "fixes": [], "error": str(e), "seconds": 0}
    raw = llm.parse_json(r["text"]) or {}
    palette = {k: raw[k] if isinstance(raw.get(k), str) and _HEX.match(raw[k]) else DEFAULT_PALETTE[k]
               for k in PALETTE_KEYS}
    palette, fixes = enforce_palette(palette)
    return palette, {"calls": r["calls"], "fixes": fixes, "seconds": r.get("seconds", 0),
                     "tools_supported": r.get("tools_supported", True),
                     "from_model": sum(1 for k in PALETTE_KEYS if k in raw)}


def ui_generator(state: PixelState):
    attempt = state["attempt"] + 1
    model, escalated = router.escalate(state["generator_model"], attempt)
    task = state["task"]
    user = (f"Бриф клиента:\n{state['brief']}\n\nЗадание: {task}\n\n"
            f"Заметки UX-исследователя:\n{state['ux_notes']}\n\n"
            "Создай HTML-файл экрана по инструкции.")
    prev_cut = attempt > 1 and "</html>" not in (state.get("html") or "").lower()
    if attempt > 1 and state.get("feedback"):
        if prev_cut:
            user += ("\n\nПредыдущая версия была ОБРЕЗАНА: ответ не поместился в лимит. "
                     "Напиши файл заново и КОМПАКТНО: CSS не длиннее ~120 строк, без комментариев, "
                     "общие стили через переменные и групповые селекторы, в таблице не больше 5 строк, "
                     "график — простой SVG до 15 элементов. Обязательно дойди до </html>.\n"
                     f"Замечания проверяющего:\n{state['feedback']}")
        else:
            user += ("\n\nЭто доработка. Предыдущая версия:\n```html\n"
                     f"{state['html'][:9000]}\n```\n\n"
                     f"Замечания проверяющего:\n{state['feedback']}\n\n"
                     "Исправь замечания и верни ПОЛНЫЙ файл.")
    feedback = state.get("feedback", "") if attempt > 1 else ""
    lessons = memory.get_lessons(_ptype(state))
    if lessons:
        user += ("\n\nУроки студии из прошлых запусков (частые ошибки на этом типе экрана):\n"
                 + "\n".join(f"- {x['text']}" for x in lessons))
    palette_info = None
    if config.PALETTE_TOOL_STEP:
        palette, palette_info = plan_palette(model, task, feedback)
        tokens = "; ".join(f"--{k.replace('_', '-')}: {v}" for k, v in palette.items())
        user += ("\n\nПалитра, проверенная инструментом check_contrast (все пары ≥ 4.5:1). "
                 f"Объяви эти токены в :root и бери цвета только из них: {tokens}")
    system, used = skills.build_system("ui_generator", _ptype(state),
                                       ("доработка " + feedback) if feedback else "")
    tier = "strong" if model == config.GENERATOR_STRONG else "light"
    r = llm.chat(model, system, user, temperature=0.4,
                 max_tokens=config.GEN_MAX_TOKENS[tier], num_ctx=12288)
    html = extract_html(r["text"])
    log = [f"[UI Generator] Skills: {', '.join(used) or 'нет'}; "
           f"уроки из памяти: {', '.join(x['category'] for x in lessons) or 'нет'}"]
    if r.get("done_reason") == "length":
        log.append(f"[UI Generator] ВНИМАНИЕ: ответ обрезан по лимиту токенов ({config.GEN_MAX_TOKENS[tier]})")
    if palette_info is not None:
        names = ", ".join(f"{c['name']}({c['args'].get('fg', c['args'].get('name', ''))}"
                          f"{'/' + c['args']['bg'] if 'bg' in c['args'] else ''})"
                          for c in palette_info["calls"]) or "нет"
        log.append(f"[UI Generator] Tool calling палитры: вызовов {len(palette_info['calls'])} [{names}], "
                   f"автоисправлений кодом {len(palette_info['fixes'])}"
                   + (f", ошибка: {palette_info['error']}" if palette_info.get("error") else ""))
    if escalated:
        log.append(f"[UI Generator] Эскалация: {_short(state['generator_model'])} не прошла проверку, "
                   f"доработку делает сильная модель {_short(model)}")
    log.append(f"[UI Generator] Попытка {attempt}/{config.MAX_ATTEMPTS}: "
               f"{len(html)} символов HTML ({_short(model)}, {r['seconds']} с)")
    return {"html": html, "attempt": attempt, "generator_model": model, "log": log}


# ---------------------------------------------------------------------------
# 4. Validator: независимая модель другой семьи + детерминированные инструменты
# ---------------------------------------------------------------------------

VALIDATOR_QUESTIONS = {
    "matches_brief": {"type": "bool",
                      "ask": "Соответствует ли страница брифу и заданию (тема, элементы, тон)?"},
    "quality": {"type": "score", "min": 0, "max": 100,
                "ask": "Общее качество структуры и вёрстки (0 = плохо, 100 = отлично)."},
}


def validator(state: PixelState):
    html = state["html"]
    task = state["task"]
    validator_model = router.pick_validator(state["generator_model"])
    log = [f"[Validator] Роутинг: генератор {_short(state['generator_model'])} "
           f"({router.family(state['generator_model'])}) -> валидатор {_short(validator_model)} "
           f"({router.family(validator_model)})"]

    # 1) Детерминированные инструменты (не LLM)
    contrast = tools.check_contrast(html)
    render = tools.render_screenshot(html)
    visible = render.get("viewports", {}).get("desktop", {}).get("visible_text")
    lint = tools.lint_html(html, task.get('product_name'), visible_text=visible)
    render_errors, render_warnings = tools.render_issues(render)
    lint["errors"] += render_errors
    lint["warnings"] += render_warnings
    if render.get("available"):
        log.append(f"[Validator] Рендер в песочнице: {render['status']}, {render['seconds']} с, "
                   f"ошибок {len(render_errors)}, предупреждений {len(render_warnings)}")
    else:
        log.append(f"[Validator] Рендер пропущен: {render['reason']}")
    log.append(f"[Validator] Инструменты: контраст, надёжных пар {contrast['certain_checked']} "
               f"(всего {contrast['checked']}), провалов {len(contrast['failures'])}, "
               f"возможных {len(contrast['possible'])}; линтер: ошибок {len(lint['errors'])}, "
               f"предупреждений {len(lint['warnings'])}")
    for p in contrast["failures"][:4]:
        log.append(f"    ✗ {p['selector']}: текст {p['fg']} на фоне {p['bg']} = {p['ratio']}:1")
    for p in contrast["possible"][:3]:
        log.append(f"    ? {p['selector']}: {p['fg']} / {p['bg']} = {p['ratio']}:1 (фон мог быть на родителе)")

    # 2) Jev-like решение валидатора (другая модельная семья)
    tool_text = (f"Контраст: надёжных пар {contrast['certain_checked']}, провалов {len(contrast['failures'])}. "
                 f"Ошибки линтера: {lint['errors'] or 'нет'}. Предупреждения: {lint['warnings'] or 'нет'}.")
    code = html if len(html) <= 6000 else html[:6000] + "\n... (код обрезан для проверки)"
    context = (f"Бриф клиента:\n{state['brief']}\n\nЗадание: {task}\n\n"
               f"Отчёт инструментов: {tool_text}\n\nКод страницы:\n{code}")
    validator_system = skills.build_system("validator", _ptype(state))[0]
    d = decide(model=validator_model, role=validator_system,
               context=context, questions=VALIDATOR_QUESTIONS,
               n_votes=config.VALIDATOR_VOTES)
    meta = d["_meta"]
    matches, quality = d["matches_brief"], d["quality"]
    log.append(f"[Validator] Решение {_short(meta['model'])} ({meta['votes_ok']}/{meta['votes_requested']} "
               f"голосов, {meta['seconds']} с): соответствует брифу={matches['value']} "
               f"(увер. {matches['confidence']}), качество={quality['value']} (увер. {quality['confidence']})")

    if meta["votes_ok"] == 0:
        log.append("[Validator] ВНИМАНИЕ: ни один голос не получен: " + " | ".join(meta["problems"]))
    elif meta["mode"] != "schema":
        log.append(f"[Validator] Примечание: строгая схема не сработала, использован режим «{meta['mode']}»")

    # 3) Вердикт
    hard_issues = []
    for p in contrast["failures"]:
        fix = tools.suggest_fix(tools.hex_to_rgb(p["fg"]), tools.hex_to_rgb(p["bg"]))
        text = (f"Низкий контраст {p['ratio']}:1 у «{p['selector']}» "
                f"(текст {p['fg']} на фоне {p['bg']}), нужно не ниже {tools.AA_NORMAL}:1.")
        options = []
        if "new_bg" in fix:
            options.append(f"заменить цвет фона {p['bg']} на {fix['new_bg']} ({fix['new_bg_ratio']}:1)")
        if "new_fg" in fix:
            options.append(f"заменить цвет текста {p['fg']} на {fix['new_fg']} ({fix['new_fg_ratio']}:1)")
        if options:
            text += " Точное исправление (рассчитано инструментом): " + " ИЛИ ".join(options) + "."
        hard_issues.append(text)
    hard_issues += [f"Линтер: {e}." for e in lint["errors"]]

    llm_ok = (matches["value"] is True and quality["value"] is not None
              and quality["value"] >= config.MIN_QUALITY)

    if hard_issues:
        outcome = "fail"
    elif matches["confidence"] < config.CONF_THRESHOLD:
        outcome = "needs_human"       # Шлюз 2: низкая уверенность -> человеку
    else:
        outcome = "pass" if llm_ok else "fail"

    feedback = "\n".join(f"- {x}" for x in hard_issues)
    if outcome == "fail" and not hard_issues:
        try:
            r = llm.chat(validator_model, validator_system,
                         f"{context}\n\nСтраница не прошла проверку (соответствие брифу="
                         f"{matches['value']}, качество={quality['value']}). "
                         "Назови 3 конкретных пункта, что исправить.",
                         temperature=0.2, max_tokens=250)
            feedback = r["text"]
        except llm.LLMError:
            feedback = "- Страница не соответствует брифу или качество ниже порога. Улучши."
    if outcome == "fail":
        feedback += "".join(f"\n- Предупреждение: {w}." for w in lint["warnings"])


    verdict = {"outcome": outcome,
               "matches_brief": matches, "quality": quality,
               "contrast_failures": contrast["failures"], "contrast_possible": contrast["possible"],
               "contrast_checked": contrast["checked"],
               "lint_errors": lint["errors"], "lint_warnings": lint["warnings"],
               "validator_model": validator_model,
               "render": {k: render.get(k) for k in ("available", "status", "reason", "seconds",
                                                    "job_dir", "blocked_requests", "page_errors")},
               "feedback": feedback}
    labels = {"pass": "PASS", "fail": "FAIL", "needs_human": "ТРЕБУЕТСЯ ЧЕЛОВЕК (низкая уверенность)"}
    log.append(f"[Validator] Вердикт: {labels[outcome]}")
    return {"verdict": verdict, "feedback": feedback, "outcome": outcome, "log": log}
