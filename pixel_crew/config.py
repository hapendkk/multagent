"""Настройки проекта Pixel Crew: модели, пороги, пути.

Все «ручки» системы собраны здесь, чтобы их было легко менять
и ссылаться на них в отчёте.
"""
import os
from pathlib import Path

# --- Ollama ---------------------------------------------------------------
OLLAMA_HOST = os.environ.get("PIXEL_OLLAMA_HOST", "http://localhost:11434")
REQUEST_TIMEOUT = 900          # секунд на один запрос к модели

# --- Модели (названия как в `ollama list`) --------------------------------
ROUTER_MODEL = "llama3.2:latest"     # Jev-like модель решений + Art Director
GENERATOR_LIGHT = "llama3.2:latest"  # для простых задач (дёшево и быстро)
GENERATOR_STRONG = "qwen3.5:4b"      # для сложных задач (лучше качество)
VALIDATOR_MODEL = "gemma4:e2b"       # другая семья (Google), чем генератор (Alibaba)

# Валидатор выбирается роутером: первый кандидат НЕ из семейства генератора
VALIDATOR_CANDIDATES = [VALIDATOR_MODEL, "phi4-mini:latest", "llama3.2:latest"]
MODEL_FAMILIES = {           # префикс названия -> семейство (разработчик)
    "llama": "llama (Meta)", "qwen": "qwen (Alibaba)", "gemma": "gemma (Google)",
    "phi": "phi (Microsoft)", "smollm": "smollm (Hugging Face)",
}

# --- Роутинг и контроль качества ------------------------------------------
LIGHT_COMPLEXITY_MAX = 35   # сложность 0..100: не выше -> лёгкая модель
STRONG_ONLY_TYPES = {"dashboard"}   # эти типы всегда на сильной модели
ESCALATE_ON_FAIL = True     # каскад: доработку после провала делает сильная модель
MAX_ATTEMPTS = 3            # максимум попыток генерации (цикл доработки)
CONF_THRESHOLD = 0.7        # ниже этой уверенности решение уходит человеку
MIN_QUALITY = 60            # минимальная оценка качества от валидатора (0..100)
ROUTER_VOTES = 3            # сколько раз опрашиваем Jev-like модель
VALIDATOR_VOTES = 5         # сколько раз опрашиваем валидатора
GEN_MAX_TOKENS = {"light": 3500, "strong": 6000}   # потолок вывода генератора по уровню модели

# --- Пути -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"
OUTPUT_DIR = BASE_DIR / "outputs"

# --- Песочница рендера (Windows Job Object) ------------------------------------
SANDBOX_ENABLED = True
SANDBOX_TIMEOUT = 90        # секунд на один рендер
SANDBOX_MEMORY_MB = 1024
SANDBOX_PIDS = 256          # лимит процессов в Job Object (Chromium плодит много)

# --- Skills и семантические .md (SOUL) ---
CREW_DIR = BASE_DIR / "crew"
SKILLS_DIR = BASE_DIR / "skills"
SKILLS_MAX = 3               # не больше 3 навыков в промпте: у моделей 3-4B мало контекста
SKILLS_BUDGET_CHARS = 3200   # общий лимит текста навыков
CREW_CONTEXT_FILES = {       # общие .md, которые получает агент, кроме SOUL.md и своей «души»
    "art_director": ["USER.md", "AGENTS.md"],
    "validator": ["TOOLS.md"],
}
DECISION_NUM_CTX = 8192      # Jev-like голоса: системный промпт с SOUL/skills + код страницы не должны обрезаться

# --- Tool calling ---
PALETTE_TOOL_STEP = True     # генератор подбирает палитру, сам проверяя её инструментом check_contrast
TOOL_MAX_ROUNDS = 4          # лимит раундов агентного цикла (защита от зацикливания)

# --- Память ---
MEMORY_ENABLED = os.environ.get("PIXEL_MEMORY", "1") == "1"
MEMORY_DB = BASE_DIR / "memory" / "pixel_memory.db"
LESSON_HALF_LIFE_DAYS = 14   # вес урока падает вдвое за 2 недели без повторения
LESSON_MIN_SCORE = 0.5       # ниже — урок не подаётся в промпт
LESSON_TTL_DAYS = 60         # урок без повторений дольше — удаляется
RUNS_TTL_DAYS = 90           # эпизоды старше — удаляются
RUNS_MAX = 500
CACHE_TTL_HOURS = 168        # кэш решений роутера: 7 дней

# --- Evals ---
JUDGE_CANDIDATES = ["phi4-mini:latest", "gemma4:e2b", "llama3.2:latest", "qwen3.5:4b"]

# --- Observability ---
TELEMETRY_ENABLED = os.environ.get("PIXEL_TELEMETRY", "1") == "1"
TELEMETRY_DIR = OUTPUT_DIR / "telemetry"     # traces.jsonl, logs.jsonl, metrics.jsonl, alerts.jsonl
ALERTS = {
    "run_seconds_max": 420,          # запуск дольше — алерт (SLO по латентности)
    "llm_tokens_per_s_min": 8,       # ниже — модель, вероятно, ушла с GPU в RAM
    "rolling_pass_rate_min": 0.5,    # pass rate по последним 10 запускам
    "rolling_window": 10,
}
