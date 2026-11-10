"""Долгосрочная память Pixel Crew (SQLite, только stdlib, локально).

Слои:
  L1 рабочая   — PixelState + чекпоинтер LangGraph (в пределах запуска, thread_id)
  L2 эпизоды   — таблица runs: что просили, что вышло, какие были замечания
  L3 уроки     — таблица lessons: повторяющиеся проблемы по типу экрана -> правило
  кэш          — таблица cache: решения Jev-like роутера по одинаковому брифу (TTL)

Запись в память ДЕТЕРМИНИРОВАННАЯ: уроки выводятся из замечаний инструментов по
словарю категорий, а не пишутся LLM. Это защищает от «отравления» памяти
галлюцинациями и prompt injection из брифа.
"""
import hashlib
import json
import re
import sqlite3
import time

import config

DAY = 86400
CATEGORIES = [
    ("contrast", ("контраст",),
     "Контраст: hover/active делай ТЕМНЕЕ базы, белый текст — только на тёмном акценте, вторичный текст не светлее #595959."),
    ("overflow", ("горизонтальн", "скролл", "overflow", "шире"),
     "Переполнение на 375 px: без фиксированных ширин > 340px и 100vw, flex-wrap и min-width:0 у детей."),
    ("name", ("название",),
     "Название продукта — ровно как в задании, в <title> и в h1/логотипе."),
    ("focus", ("focus",), "У всех кнопок и ссылок есть :focus-visible."),
    ("targets", ("24×24", "24x24", "цели", "мелк"), "Кнопки и ссылки не меньше 44×44 px."),
    ("js", ("javascript", "js", "консол"), "Не используй JS без необходимости; страница должна работать без ошибок в консоли."),
    ("external", ("внешн", "сеть", "запрос"), "Никаких внешних ресурсов: шрифты системные, картинки — CSS/inline SVG."),
    ("meta", ("viewport", "lang", "title"), "Обязательны <html lang>, meta viewport и непустой <title>."),
    ("layout", ("max-width", "центр", "@media", "font-family"), "Каркас .app: max-width + margin 0 auto, font-family, @media."),
]
_LESSON_TEXT = {k: t for k, _, t in CATEGORIES}


def _conn():
    config.MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.MEMORY_DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, ts REAL, brief TEXT, product_type TEXT,
        complexity INTEGER, generator_model TEXT, outcome TEXT, attempts INTEGER, quality INTEGER,
        categories TEXT, product_name TEXT);
    CREATE TABLE IF NOT EXISTS lessons(category TEXT, product_type TEXT, count INTEGER, resolved INTEGER,
        first_seen REAL, last_seen REAL, PRIMARY KEY(category, product_type));
    CREATE TABLE IF NOT EXISTS cache(kind TEXT, key TEXT, value TEXT, ts REAL, PRIMARY KEY(kind, key));
    """)
    return c


def categorize(issue):
    low = issue.lower()
    for key, words, _ in CATEGORIES:
        if any(w in low for w in words):
            return key
    return None


def _tokens(text):
    return set(re.findall(r"\w{4,}", text.lower()))


# --- кэш -------------------------------------------------------------------

def _key(text):
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()


def cache_get(kind, text, now=None):
    if not config.MEMORY_ENABLED:
        return None
    now = now or time.time()
    with _conn() as c:
        row = c.execute("SELECT value, ts FROM cache WHERE kind=? AND key=?", (kind, _key(text))).fetchone()
    if row and now - row[1] <= config.CACHE_TTL_HOURS * 3600:
        return json.loads(row[0])
    return None


def cache_put(kind, text, value, now=None):
    if config.MEMORY_ENABLED:
        with _conn() as c:
            c.execute("REPLACE INTO cache VALUES(?,?,?,?)",
                      (kind, _key(text), json.dumps(value, ensure_ascii=False), now or time.time()))


# --- эпизоды и уроки -----------------------------------------------------------

def _val(route, k):
    v = route.get(k)
    return v.get("value") if isinstance(v, dict) else v


def record_run(state, feedbacks, now=None):
    """Сохраняет эпизод и обновляет уроки. feedbacks — замечания валидатора по попыткам."""
    if not config.MEMORY_ENABLED or state.get("outcome") == "rejected":
        return {}
    now = now or time.time()
    ptype = _val(state.get("route", {}), "product_type") or "other"
    per_attempt = [{categorize(line) for line in fb.splitlines() if line.strip()} - {None} for fb in feedbacks]
    seen = set().union(*per_attempt) if per_attempt else set()
    resolved = seen - per_attempt[-1] if state.get("outcome") == "pass" and per_attempt else (
        seen if state.get("outcome") == "pass" else set())
    q = (state.get("verdict") or {}).get("quality")
    quality = q.get("value") if isinstance(q, dict) else q
    with _conn() as c:
        c.execute("INSERT INTO runs(ts, brief, product_type, complexity, generator_model, outcome, attempts,"
                  " quality, categories, product_name) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (now, state["brief"][:2000], ptype, _val(state["route"], "complexity"),
                   state.get("generator_model"), state.get("outcome"), state.get("attempt"),
                   quality, json.dumps(sorted(seen)),
                   (state.get("task") or {}).get("product_name")))
        for cat in seen:
            c.execute("""INSERT INTO lessons VALUES(?,?,1,?,?,?)
                ON CONFLICT(category, product_type) DO UPDATE SET count=count+1,
                resolved=resolved+excluded.resolved, last_seen=excluded.last_seen""",
                      (cat, ptype, int(cat in resolved), now, now))
    pruned = prune(now)
    return {"categories": sorted(seen), "resolved": sorted(resolved), "pruned": pruned}


def lesson_score(count, resolved, last_seen, now):
    """Вес урока: частота × затухание по времени; урок, который всегда легко
    исправляется с первой доработки, весит меньше."""
    decay = 0.5 ** ((now - last_seen) / DAY / config.LESSON_HALF_LIFE_DAYS)
    return round(count * decay * (1 - 0.5 * resolved / max(count, 1)), 2)


def get_lessons(product_type, k=3, now=None):
    if not config.MEMORY_ENABLED:
        return []
    now = now or time.time()
    with _conn() as c:
        rows = c.execute("SELECT category, product_type, count, resolved, last_seen FROM lessons "
                         "WHERE product_type IN (?, 'any')", (product_type or "other",)).fetchall()
    scored = [(lesson_score(cnt, res, ls, now), cat, cnt) for cat, _, cnt, res, ls in rows]
    scored = [s for s in scored if s[0] >= config.LESSON_MIN_SCORE]
    scored.sort(reverse=True)
    return [{"category": cat, "score": sc, "count": cnt, "text": _LESSON_TEXT[cat]} for sc, cat, cnt in scored[:k]]


def similar_runs(brief, product_type=None, k=2, min_sim=0.2):
    if not config.MEMORY_ENABLED:
        return []
    q = _tokens(brief)
    with _conn() as c:
        rows = c.execute("SELECT brief, product_type, outcome, attempts, categories FROM runs "
                         "ORDER BY ts DESC LIMIT 200").fetchall()
    out = []
    for b, pt, outcome, attempts, cats in rows:
        t = _tokens(b)
        sim = len(q & t) / len(q | t) if q and t else 0
        if product_type and pt == product_type:
            sim += 0.1
        if sim >= min_sim:
            out.append({"brief": b[:200], "outcome": outcome, "attempts": attempts,
                        "issues": json.loads(cats or "[]"), "similarity": round(sim, 2)})
    return sorted(out, key=lambda x: -x["similarity"])[:k]


def prune(now=None):
    """Правила актуализации: удалить устаревшие уроки, эпизоды и кэш."""
    now = now or time.time()
    with _conn() as c:
        rows = c.execute("SELECT category, product_type, count, resolved, last_seen FROM lessons").fetchall()
        stale = [(cat, pt) for cat, pt, cnt, res, ls in rows
                 if now - ls > config.LESSON_TTL_DAYS * DAY and lesson_score(cnt, res, ls, now) < config.LESSON_MIN_SCORE]
        c.executemany("DELETE FROM lessons WHERE category=? AND product_type=?", stale)
        r1 = c.execute("DELETE FROM runs WHERE ts < ?", (now - config.RUNS_TTL_DAYS * DAY,)).rowcount
        r2 = c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY ts DESC LIMIT ?)",
                       (config.RUNS_MAX,)).rowcount
        r3 = c.execute("DELETE FROM cache WHERE ts < ?", (now - config.CACHE_TTL_HOURS * 3600,)).rowcount
    return {"lessons": len(stale), "runs": r1 + r2, "cache": r3}
