# Pixel Crew: мультиагентная дизайн-студия (каркас v0.1)

Бриф на естественном языке -> Art Director (Jev-like роутинг) -> UX Researcher ->
UI Generator -> Validator (другая модельная семья + инструменты) -> цикл доработки.

## Быстрый старт (Windows, PowerShell)
```powershell
cd D:\agent\pixel_crew
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --no-cache-dir -r requirements.txt
python -m playwright install chromium   # браузер для песочницы рендера
python main.py "Мобильное приложение для медитации: экран выбора длительности сессии"
```
Нужна запущенная Ollama и модели: llama3.2, qwen3.5:4b, gemma4:e2b.

Песочница рендера — Windows Job Object + песочница Chromium + блокировка сети в
Playwright (`sandbox/windows_runner.py`), см. `docs/04_isolation.md`. Нужен один раз
`python -m playwright install chromium` в активном venv.

## Файлы
- `config.py`: модели, пороги, пути
- `state.py`: общее состояние («общая доска»)
- `llm.py`: обёртка над API Ollama
- `skills.py`: сборка системного промпта (SOUL + душа агента + роль + skills)
- `crew/`: SOUL.md, AGENTS.md, USER.md, TOOLS.md, souls/ — семантические .md команды
- `skills/*/SKILL.md`: навыки агентов, подключаются по типу экрана и замечаниям
- `agent_tools.py`: инструменты, которые агент вызывает сам (tool calling), allowlist по агентам
- `memory.py`: долгосрочная память (SQLite): эпизоды, уроки с затуханием, кэш роутера
- `observability.py`: трейсы, логи, метрики, алерты, траектории (outputs/telemetry/); `python observability.py` — сводка
- `router.py`: роутинг по моделям (лёгкая/сильная, каскад, валидатор другого семейства)
- `decision_model.py`: Jev-like модель решений (схема ответа + голосование)
- `tools.py`: контраст WCAG, линтер, рендер в песочнице
- `sandbox/`: песочница рендера HTML (`windows_runner.py` — Job Object, `render_worker.py` — Playwright внутри)
- `tests/test_routing.py`, `tests/test_graph_mock.py`: тесты роутинга и графа без Ollama (`python -m pytest tests`)
- `tests/test_windows_sandbox.py`: проверка лимитов Job Object и маршрута рендера
- `docs/`: разделы отчёта
- `agents.py`: четыре агента
- `graph.py`: граф LangGraph (шлюзы и цикл доработки)
- `main.py`: запуск
- `prompts/`: системные промпты агентов
- `outputs/`: результаты запусков (создаётся автоматически)
