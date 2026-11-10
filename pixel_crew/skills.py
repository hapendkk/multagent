"""Skills и семантические .md: сборка системного промпта агента.

Промпт = crew/SOUL.md (общие ценности) + crew/souls/<агент>.md (характер)
       + общие файлы из CREW_CONTEXT_FILES + prompts/<агент>.md (роль)
       + до SKILLS_MAX навыков из skills/*/SKILL.md.

Навыки подключаются выборочно (progressive disclosure): по агенту, типу экрана
и ключевым словам из замечаний валидатора, в пределах SKILLS_BUDGET_CHARS.
"""
from functools import lru_cache

import config


def _parse_front_matter(text):
    if not text.startswith("---"):
        return {}, text
    head, body = text[3:].split("\n---", 1)
    meta = {}
    for line in head.strip().splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif value in ("true", "false"):
            value = value == "true"
        meta[key.strip()] = value
    return meta, body.strip()


@lru_cache(maxsize=1)
def load_skills():
    skills = []
    for path in sorted(config.SKILLS_DIR.glob("*/SKILL.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        meta.setdefault("name", path.parent.name)
        skills.append({**meta, "body": body})
    return skills


def select_skills(agent, product_type=None, feedback=""):
    """Возвращает список выбранных навыков (по убыванию релевантности)."""
    fb = (feedback or "").lower()
    scored = []
    for s in load_skills():
        if agent not in s.get("agents", []):
            continue
        types = s.get("product_types", ["any"])
        score = 0
        if product_type and product_type in types:
            score += 3
        if any(t.lower() in fb for t in s.get("triggers", [])):
            score += 4
        if s.get("always"):
            score += 1
        if score == 0 and "any" not in types:
            continue
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    chosen, used = [], 0
    for _, s in scored:
        if len(chosen) >= config.SKILLS_MAX or used + len(s["body"]) > config.SKILLS_BUDGET_CHARS:
            continue
        chosen.append(s)
        used += len(s["body"])
    return chosen


def _read(path):
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def build_system(agent, product_type=None, feedback=""):
    """Возвращает (системный промпт, список имён навыков)."""
    parts = [_read(config.CREW_DIR / "SOUL.md"), _read(config.CREW_DIR / "souls" / f"{agent}.md")]
    parts += [_read(config.CREW_DIR / f) for f in config.CREW_CONTEXT_FILES.get(agent, [])]
    parts.append(_read(config.PROMPTS_DIR / f"{agent}.md"))
    chosen = select_skills(agent, product_type, feedback)
    if chosen:
        parts.append("# Подключённые навыки (skills)\n\n" + "\n\n".join(s["body"] for s in chosen))
    return "\n\n".join(p for p in parts if p), [s["name"] for s in chosen]
