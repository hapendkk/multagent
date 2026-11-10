"""Инструменты агентов. Всё, что можно посчитать точно, считает код, а не LLM.

Результаты тестов показали: ни одна из 7 моделей не считает контраст WCAG
правильно, поэтому расчёт сделан детерминированно по формуле.
"""
import colorsys
import re

AA_NORMAL = 4.5   # минимальный контраст WCAG AA для обычного текста

_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0)}


# --- Контраст WCAG ----------------------------------------------------------

def _linear(c):
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_linear(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """Коэффициент контраста WCAG между двумя RGB-цветами."""
    l1, l2 = luminance(fg), luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h[:3])
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(c)) for c in rgb)


def _shift_until(color, other, target):
    """Двигает светлоту `color` (сохраняя оттенок) от `other`, пока контраст не достигнет target.
    Возвращает (hex, ratio) или None, если цель недостижима."""
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in color))
    direction = -1 if luminance(color) <= luminance(other) else 1
    # если цвет светлее другого, осветляем; если темнее, затемняем
    for step in range(1, 101):
        nl = min(1.0, max(0.0, l + direction * step / 100))
        rgb = tuple(c * 255 for c in colorsys.hls_to_rgb(h, nl, s))
        ratio = contrast_ratio(tuple(int(round(c)) for c in rgb), other)
        if ratio >= target:
            return _hex(rgb), round(ratio, 2)
        if nl in (0.0, 1.0):
            break
    return None


def suggest_fix(fg, bg, target=AA_NORMAL):
    """Ближайшие исправления пары цветов до порога AA: изменить фон ИЛИ изменить текст.
    Оттенок сохраняется, меняется только светлота."""
    out = {}
    new_bg = _shift_until(bg, fg, target)
    if new_bg:
        out["new_bg"], out["new_bg_ratio"] = new_bg
    new_fg = _shift_until(fg, bg, target)
    if new_fg:
        out["new_fg"], out["new_fg_ratio"] = new_fg
    return out


def _resolve(value, css_vars):
    """Подставляет значения CSS-переменных var(--имя) в строку."""
    value = (value or "").strip().lower()
    for _ in range(5):
        m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)", value)
        if not m:
            break
        repl = css_vars.get(m.group(1)) or m.group(2) or ""
        value = value[:m.start()] + repl.strip() + value[m.end():]
    return value


def _color(value, css_vars):
    """Возвращает (rgb, непрозрачный). Градиенты и прозрачность считаем непригодными
    для точной проверки: (None, False)."""
    v = _resolve(value, css_vars)
    if not v or "gradient" in v or "transparent" in v or "inherit" in v or "currentcolor" in v:
        return None, False
    m = re.search(r"#([0-9a-f]{3,8})\b", v)
    if m:
        h = m.group(1)
        if len(h) not in (3, 4, 6, 8):
            return None, False
        opaque = not ((len(h) == 4 and h[3] != "f") or (len(h) == 8 and h[6:] != "ff"))
        return hex_to_rgb(h), opaque
    m = re.search(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)\s*(?:[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        rgb = tuple(min(255, int(x)) for x in m.groups()[:3])
        alpha = 1.0
        if m.group(4):
            a = m.group(4)
            alpha = float(a[:-1]) / 100 if a.endswith("%") else float(a)
        return rgb, alpha >= 0.99
    for name, rgb in _NAMED.items():
        if re.search(rf"\b{name}\b", v):
            return rgb, True
    return None, False


def _style_text(html):
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _strip_dark_media(css):
    """Убирает блоки @media (prefers-color-scheme: dark): проверяем светлую тему."""
    out, i = [], 0
    pat = re.compile(r"@media[^{]*prefers-color-scheme\s*:\s*dark[^{]*\{", re.I)
    while True:
        m = pat.search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def _decl(body, prop_regex):
    m = re.search(rf"(?<![\w-])(?:{prop_regex})\s*:\s*([^;]+)", body)
    return m.group(1).strip() if m else None


def check_contrast(html):
    """Проверяет контраст пар «цвет текста / фон» из CSS страницы (светлая тема).

    «Надёжная» пара: цвет текста и фон заданы в ОДНОМ правиле, оба непрозрачные.
    Только такие провалы считаются ошибками.
    «Возможная» пара: задан только один из цветов, второй берётся с body (наследование).
    В реальном CSS фон часто лежит на родителе в другом правиле, поэтому такие
    провалы это лишь предупреждения. Точный ответ даст рендер в браузере
    (шаг про песочницу).
    Не учитывает: градиенты, картинки, прозрачность, крупный текст (порог 3:1).
    """
    css = _strip_dark_media(_style_text(html))
    rules = [(" ".join(s.split()), b)
             for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]

    css_vars = {}
    for sel, body in rules:
        if ":root" in sel or sel in ("html", "body"):
            for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body):
                css_vars[name] = val.strip()

    page_bg, page_fg = (255, 255, 255), (0, 0, 0)
    for sel, body in rules:
        if sel in ("body", "html", "html, body", "body, html", ":root"):
            bg, bg_ok = _color(_decl(body, "background-color|background"), css_vars)
            fg, fg_ok = _color(_decl(body, "color"), css_vars)
            if bg and bg_ok:
                page_bg = bg
            if fg and fg_ok:
                page_fg = fg

    # Базовые цвета правил (для :hover и т.п. недостающий цвет берём отсюда)
    state_re = re.compile(r":(?:hover|focus-visible|focus-within|focus|active)\b")
    base = {}
    for sel, body in rules:
        if sel.startswith("@") or state_re.search(sel):
            continue
        f, f_ok = _color(_decl(body, "color"), css_vars)
        b, b_ok = _color(_decl(body, "background-color|background"), css_vars)
        base[sel] = (f if f_ok else None, b if b_ok else None)

    pairs, seen = [], set()
    for sel, body in rules:
        if sel.startswith("@"):
            continue
        fg_raw = _decl(body, "color")
        bg_raw = _decl(body, "background-color|background")
        fg, fg_ok = _color(fg_raw, css_vars)
        bg, bg_ok = _color(bg_raw, css_vars)
        # объявленный, но непригодный цвет (градиент, прозрачность): не судим
        if (fg_raw and not (fg and fg_ok)) or (bg_raw and not (bg and bg_ok)):
            continue
        if not fg and not bg:
            continue
        certain = bool(fg and bg)
        if not certain and state_re.search(sel):
            base_fg, base_bg = base.get(state_re.sub("", sel).strip(), (None, None))
            fg, bg = fg or base_fg, bg or base_bg
            certain = bool(fg and bg)
        fg, bg = fg or page_fg, bg or page_bg
        key = (sel, fg, bg)
        if key in seen:
            continue
        seen.add(key)
        ratio = round(contrast_ratio(fg, bg), 2)
        pairs.append({"selector": sel[:60],
                      "fg": "#%02x%02x%02x" % fg, "bg": "#%02x%02x%02x" % bg,
                      "ratio": ratio, "pass": ratio >= AA_NORMAL, "certain": certain})
    failing = [p for p in pairs if not p["pass"]]
    return {"checked": len(pairs),
            "certain_checked": sum(1 for p in pairs if p["certain"]),
            "pairs": pairs,
            "failures": [p for p in failing if p["certain"]],
            "possible": [p for p in failing if not p["certain"]]}


# --- Линтер -----------------------------------------------------------------

# Похожие буквы: модели путают кириллицу и латиницу в названиях («Сereni» с русской С)
_CYR_TO_LAT = str.maketrans("АВЕКМНОРСТУХаеорсухкмтв", "ABEKMHOPCTYXaeopcyxkmtb")
_LAT_TO_CYR = str.maketrans("ABEKMHOPCTYXaeopcyx", "АВЕКМНОРСТУХаеорсух")


def fix_mixed_script(name):
    """В словах, где смешаны кириллица и латиница, приводит буквы к преобладающему алфавиту."""
    words = []
    for w in name.split():
        cyr = sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in w)
        lat = sum("a" <= c.lower() <= "z" for c in w)
        if cyr and lat:
            w = w.translate(_CYR_TO_LAT) if lat >= cyr else w.translate(_LAT_TO_CYR)
        words.append(w)
    return " ".join(words)


def _norm(text):
    return text.casefold().replace("ё", "е").translate(_CYR_TO_LAT)


def name_presence(name, text, min_ratio=0.8):
    """exact | similar | missing: есть ли название на странице (с учётом похожих букв и опечаток)."""
    from difflib import SequenceMatcher
    n, t = _norm(name).strip(), _norm(text)
    if not n or n in t:
        return "exact"
    k = len(n.split())
    tokens = re.findall(r"\w+", t)
    for i in range(len(tokens) - k + 1):
        if SequenceMatcher(None, n, " ".join(tokens[i:i + k])).ratio() >= min_ratio:
            return "similar"
    return "missing"


def lint_html(html, product_name=None, visible_text=None):
    """Простые проверки структуры и доступности. Возвращает errors и warnings."""
    low = html.lower()
    errors, warnings = [], []
    if "</html>" not in low:
        errors.append("HTML не закрыт (возможно, ответ модели обрезан)")
    if not re.search(r"<html[^>]*\blang\s*=", low):
        errors.append("у тега <html> нет атрибута lang")
    if not re.search(r'<meta[^>]+name\s*=\s*["\']viewport', low):
        errors.append("нет meta viewport (страница не будет адаптивной на телефоне)")
    if not re.search(r"<title>\s*\S", low):
        errors.append("нет непустого <title>")
    for tag in re.findall(r"<img\b[^>]*>", low):
        if "alt=" not in tag:
            errors.append("есть <img> без атрибута alt")
            break
    if "<h1" not in low:
        warnings.append("нет заголовка <h1>")
    if not re.search(r"@media|flex|grid|clamp\(", low):
        warnings.append("не видно признаков адаптивной вёрстки (@media, flex, grid)")
    # Требования проекта к вёрстке (по наблюдениям дизайнера)
    if not re.search(r"font-family\s*:|font\s*:\s*[^;]*\b(?:sans-serif|serif|system-ui)", low):
        errors.append("не задан font-family (будет дефолтный шрифт браузера); "
                      "используй стек вроде system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif")
    has_width = bool(re.search(r"max-width\s*:", low))
    has_center = bool(re.search(
        r"margin(?:-inline)?\s*:\s*[^;{}]*\bauto\b|justify-content\s*:\s*center|"
        r"place-(?:items|content)\s*:\s*center|align-items\s*:\s*center", low))
    if not (has_width and has_center):
        errors.append("контейнер не ограничен по ширине и не центрирован: "
                      "нужен max-width (например 480px) и margin: 0 auto, "
                      "иначе на широком экране всё прижмётся к краю")
    if product_name and product_name != "Проект":
        title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        text = (visible_text or re.sub(r"<[^>]+>", " ", html)) + " " + (title.group(1) if title else "")
        presence = name_presence(product_name, text)
        if presence == "missing":
            errors.append(f"название проекта «{product_name}» не показано на странице "
                          "(добавь его в шапку и в <title>)")
        elif presence == "similar":
            warnings.append(f"название проекта на странице написано иначе, чем «{product_name}»: "
                            "используй точное написание")
    return {"errors": errors, "warnings": warnings}


# --- Рендер (песочница Windows Job Object) ------------------------------------

def render_screenshot(html):
    """Рендер в песочнице (sandbox/windows_runner.py): Job Object с лимитами,
    Chromium с собственной песочницей, скриншоты 375 и 1280 px, горизонтальный
    скролл, размер кликабельных зон, ошибки JS, попытки сетевых запросов."""
    import observability as obs
    from sandbox import windows_runner
    with obs.span("sandbox.render", "sandbox", runtime="windows-job") as a:
        res = windows_runner.render(html)
        a["available"], a["status"] = res.get("available"), res.get("status")
        return res


def render_issues(render):
    """Переводит результат рендера в ошибки и предупреждения для валидатора."""
    errors, warnings = [], []
    if not render.get("available"):
        return errors, warnings
    if render.get("status") != "ok":
        warnings.append(f"рендер в песочнице не удался ({render.get('status')})")
        return errors, warnings
    mobile = render["viewports"].get("mobile", {})
    if mobile.get("horizontal_overflow"):
        errors.append(f"на телефоне (ширина {mobile['viewport_width']} px) появляется горизонтальная "
                      f"прокрутка: ширина страницы {mobile['scroll_width']} px. Убери фиксированные "
                      "ширины, используй max-width: 100%, box-sizing: border-box")
    for e in render.get("page_errors", [])[:3]:
        errors.append(f"ошибка JavaScript при загрузке: {e}")
    small = mobile.get("small_targets", [])
    if small:
        warnings.append("кликабельные элементы меньше 24×24 px (WCAG 2.5.8): " + "; ".join(small[:4]))
    if render.get("blocked_requests"):
        warnings.append("страница пытается загрузить внешние ресурсы (заблокировано, сети нет): "
                        + ", ".join(render["blocked_requests"][:3]))
    for e in render.get("console_errors", [])[:2]:
        warnings.append(f"ошибка в консоли браузера: {e}")
    return errors, warnings
