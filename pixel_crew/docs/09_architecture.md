# 9. Архитектура системы

## 9.1. Выбранный паттерн и почему

Сначала я вообще не понимала, что от меня хотят под «мультиагентной системой» — грозное слово, а по факту нужен конвейер «бриф → экран → проверка». Перебрала паттерны один за другим и для каждого спрашивала себя: «а выдержит ли это модель 3B?» Таблица ниже — итог этих раздумий:

Pixel Crew — **оркестрируемый граф агентов** (orchestrator–workers на LangGraph). Внутри графа есть цикл **evaluator–optimizer** (генератор ⇄ независимый валидатор) и общее состояние в стиле **blackboard** (`PixelState`).

| Паттерн | Суть | Плюсы | Минусы | У нас |
|---|---|---|---|---|
| Один агент (ReAct) | Одна LLM в цикле «мысль → действие → наблюдение» | Просто | Проверяет сам себя, контекст переполняется, маленькая модель теряет нить | ✗ |
| Конвейер (prompt chaining) | Фиксированная цепочка шагов | Предсказуемо, дёшево | Нет обратной связи и исправления ошибок | Основа потока |
| **Оркестратор + исполнители** | Центральный узел ставит задачу и маршрутизирует | Контроль, роутинг по сложности | Оркестратор — единая точка отказа | ✓ Art Director |
| **Evaluator–optimizer** | Генератор ⇄ оценщик до прохождения порога | Качество растёт с попытками | Время ×N, нужен лимит попыток | ✓ цикл доработки, ≤ 3 попытки |
| Supervisor / иерархия (LLM решает, кого звать) | LLM-менеджер выбирает следующего агента | Гибкость | На 3–4B моделях ненадёжно, сложно отлаживать | ✗ переходы решает код |
| Хореография / swarm | Агенты передают управление друг другу сами | Нет центра, масштабируется | Трудно гарантировать завершение и порядок | ✗ |
| Debate / multi-agent chat | Агенты обсуждают решение | Разнообразие мнений | Многословно и дорого по токенам | ✗ (вместо этого — N голосов Jev-like) |
| **Blackboard** | Общее хранилище, из которого агенты читают и куда пишут | Слабая связность агентов | Нужна дисциплина схемы состояния | ✓ `PixelState` |

**Почему так — коротко:** я долго думала, отдать ли управление потоком LLM (как в CrewAI hierarchical или AutoGen group chat). Отказалась: на моделях 3–4B «менеджер» будет тупить и зацикливаться, а отлаживать это — кошмар. Поэтому LLM даёт **оценки** (Jev-like решения с уверенностью), а **переходы** выбирает код с порогами. Проверка отделена от генерации и выполняется **моделью другого семейства** вместе с детерминированными инструментами — это и есть слой верификации из ТЗ.

### Слои агента

| Слой | Что входит |
|---|---|
| 1. Интерфейс | CLI `main.py` → далее HTTP API/UI |
| 2. Оркестрация | LangGraph: граф, шлюзы, цикл, лимит попыток, human-in-the-loop |
| 3. Принятие решений | Jev-like модель (`decision_model.py`) + роутер моделей (`router.py`) |
| 4. Агенты | Art Director, UX Researcher, UI Generator (роли = промпты + skills + SOUL-файлы) |
| 5. **Верификация и валидация** | Validator: инструменты (контраст, линтер, рендер в песочнице) + LLM другого семейства + пороги + `needs_human` |
| 6. Инструменты | `tools.py`, `sandbox/` (Job Object) |
| 7. Память | short-term: состояние и чекпоинты; long-term: история запусков и уроки дизайна |
| 8. Модели | Ollama: llama3.2, qwen3.5:4b, gemma4:e2b, phi4-mini |
| 9. Наблюдаемость | логи, трейсы, метрики, алерты, траектории (раздел 16) |

## 9.2. Диаграммы C4

Семь диаграмм по c4model.com: 4 основных уровня (Context, Container, Component, Code) и 3 дополнительных (System Landscape, Dynamic, Deployment). Формат — Mermaid: исходник рендерится на GitHub, в VS Code и на mermaid.live.

### 9.2.1. System Landscape — Pixel Crew в окружении

```mermaid
C4Context
title System Landscape: Pixel Crew на ноутбуке разработчика
Person(designer, "Дизайнер / продакт", "Пишет бриф, получает экран")
Person(reviewer, "Ревьюер", "Решает спорные случаи needs_human")
Enterprise_Boundary(laptop, "Ноутбук: Windows 10, RTX 4050 6 ГБ") {
  System(pixel, "Pixel Crew", "Мультиагентная дизайн-студия: бриф → проверенный HTML")
  System_Ext(ollama, "Ollama", "Локальный инференс LLM на GPU")
  System_Ext(obs, "Observability", "JSONL в формате OTel: трейсы, логи, метрики, алерты")
  System_Ext(fs, "Файлы результатов", "outputs/: screen.html, report.json, скриншоты")
}
Rel(designer, pixel, "Бриф", "CLI")
Rel(pixel, designer, "Экран + отчёт")
Rel(reviewer, pixel, "Решение по needs_human")
Rel(pixel, ollama, "Запросы к LLM", "HTTP :11434")
Rel(pixel, obs, "Трейсы, метрики", "OTLP")
Rel(pixel, fs, "Пишет артефакты")
```

### 9.2.2. System Context

```mermaid
C4Context
title System Context: Pixel Crew
Person(designer, "Дизайнер / продакт", "Автор брифа")
System(pixel, "Pixel Crew", "Принимает бриф, генерирует экран, проверяет на бриф, WCAG AA и адаптивность")
System_Ext(ollama, "Ollama (Windows-хост)", "llama3.2 · qwen3.5:4b · gemma4:e2b · phi4-mini")
System_Ext(obs, "Observability backend", "outputs/telemetry: трейсы, метрики, алерты (OTel-совместимо)")
Rel(designer, pixel, "Отправляет бриф, получает screen.html и report.json")
Rel(pixel, ollama, "chat / structured output", "HTTP JSON")
Rel(pixel, obs, "Телеметрия", "OTLP")
```

### 9.2.3. Container

```mermaid
C4Container
title Container: Pixel Crew
Person(designer, "Дизайнер")
System_Ext(ollama, "Ollama", "LLM на GPU, Windows-хост")
System_Boundary(win, "Windows-хост: Pixel Crew") {
  Container(cli, "CLI", "Python, main.py", "Принимает бриф, печатает шаги, сохраняет отчёт")
  Container(orch, "Оркестратор агентов", "Python, LangGraph", "Граф агентов, шлюзы, цикл доработки, роутинг")
  Container(sandbox, "Песочница рендера", "Job Object + Chromium/Playwright", "Лимиты памяти/процессов, песочница Chromium, блокировка сети")
  ContainerDb(mem, "Память", "InMemorySaver + SQLite", "L1 рабочая, L2 эпизоды, L3 уроки, кэш роутера")
  ContainerDb(out, "Артефакты", "Файлы outputs/", "screen.html, report.json, скриншоты")
  Container(kb, "База знаний агентов", "Markdown", "Системные промпты, 9 skills, SOUL-файлы")
}
System_Ext(obs, "Observability", "observability.py → outputs/telemetry")
Rel(designer, cli, "Бриф")
Rel(cli, orch, "invoke / stream")
Rel(orch, ollama, "LLM-запросы", "HTTP :11434")
Rel(orch, sandbox, "HTML на рендер", "Job Object + Chromium")
Rel(orch, mem, "Читает / пишет")
Rel(orch, kb, "Загружает промпты и skills")
Rel(orch, out, "Сохраняет")
Rel(orch, obs, "Трейсы, метрики", "OTLP")
```

### 9.2.4. Component — оркестратор

```mermaid
C4Component
title Component: оркестратор агентов (LangGraph)
Container_Boundary(orch, "Оркестратор") {
  Component(graph, "Graph", "graph.py", "Узлы, рёбра, шлюзы 1 и 2, цикл ≤ 3")
  Component(state, "PixelState", "state.py", "Общая доска: бриф, задание, HTML, вердикт, журнал")
  Component(ad, "Art Director", "agents.py", "Классификация брифа, задание, роутинг")
  Component(ux, "UX Researcher", "agents.py", "Персона, сценарий, структура, риски доступности")
  Component(gen, "UI Generator", "agents.py", "HTML-экран, доработка по замечаниям")
  Component(val, "Validator", "agents.py", "Слой верификации: инструменты + LLM другого семейства + вердикт")
  Component(jev, "Jev-like Decision Model", "decision_model.py", "Типизированные ответы, N голосов, confidence")
  Component(router, "Model Router", "router.py", "light/strong, каскад, выбор валидатора")
  Component(tools, "Tools", "tools.py", "WCAG-контраст, линтер, проверка названия")
  Component(sbx, "Sandbox Runner", "sandbox/windows_runner.py", "Job Object, лимиты, kill-on-close, fail-closed")
  Component(llm, "LLM Client", "llm.py", "Ollama /api/chat, schema, think=false")
}
Rel(graph, ad, "вызывает")
Rel(graph, ux, "вызывает")
Rel(graph, gen, "вызывает")
Rel(graph, val, "вызывает")
Rel(ad, jev, "классификация")
Rel(ad, router, "выбор генератора")
Rel(val, jev, "оценка")
Rel(val, router, "выбор валидатора")
Rel(val, tools, "проверки")
Rel(tools, sbx, "рендер")
Rel(jev, llm, "голоса")
Rel(gen, llm, "генерация")
UpdateLayoutConfig($c4ShapeInRow="4", $c4BoundaryInRow="1")
```

### 9.2.5. Code — ключевые классы и функции

```mermaid
classDiagram
class PixelState {
  +str brief
  +dict task
  +dict route
  +str generator_model
  +str ux_notes
  +str html
  +str feedback
  +dict verdict
  +int attempt
  +str outcome
  +list log  (reducer: operator.add)
}
class agents {
  +art_director(state) dict
  +ux_researcher(state) dict
  +ui_generator(state) dict
  +validator(state) dict
}
class decision_model {
  +decide(model, role, context, questions, n_votes) dict
}
class router {
  +route_generator(complexity, product_type) tuple
  +escalate(model, attempt) tuple
  +pick_validator(generator_model) str
  +family(model) str
}
class tools {
  +contrast_ratio(fg, bg) float
  +check_contrast(html) dict
  +lint_html(html, product_name, visible_text) dict
  +name_presence(name, text) str
  +render_screenshot(html) dict
  +render_issues(render) tuple
}
class runner {
  +render(html) dict
  -_oci_config(job_in, job_out) dict
  -_limits_prefix() list
}
class llm {
  +chat(model, system, user, schema) dict
  +parse_json(text) dict
}
class graph {
  +build_graph() CompiledGraph
  +after_art_director(state) str
  +after_validator(state) str
}
graph ..> agents
graph ..> PixelState
agents ..> decision_model
agents ..> router
agents ..> tools
agents ..> llm
decision_model ..> llm
tools ..> runner
```

### 9.2.6. Dynamic — обработка брифа с доработкой

```mermaid
C4Dynamic
title Dynamic: бриф → FAIL → эскалация → PASS
Person(designer, "Дизайнер")
Container(cli, "CLI", "main.py")
Component(ad, "Art Director", "llama3.2")
Component(ux, "UX Researcher", "модель по роутингу")
Component(gen, "UI Generator", "light → strong")
Component(val, "Validator", "другое семейство + инструменты")
Container(sbx, "Песочница (Job Object)", "Chromium")
Rel(designer, cli, "Бриф → получает screen.html + report.json")
Rel(cli, ad, "invoke(state)")
Rel(ad, ux, "Задание + модель (шлюз 1 пройден)")
Rel(ux, gen, "UX-заметки")
Rel(gen, val, "HTML: попытка 1 llama3.2 → попытка 2 qwen3.5:4b")
Rel(val, sbx, "Рендер 375/1280 px")
Rel(val, gen, "FAIL + точные исправления (эскалация)")
Rel(val, cli, "PASS, вердикт")
UpdateRelStyle(gen, val, $offsetY="-30")
UpdateRelStyle(val, gen, $offsetY="30")
```

### 9.2.7. Deployment

```mermaid
C4Deployment
title Deployment: ноутбук Windows 10
Deployment_Node(laptop, "Ноутбук", "Windows 10 Home, 16 ГБ RAM, RTX 4050 6 ГБ") {
  Container(ollama, "Ollama", "llama.cpp, CUDA", "Модели Q4_K_M, по одной в VRAM, keep_alive 10m")
  Container(app, "Pixel Crew", "Python 3.12, LangGraph", "Оркестратор и агенты")
  ContainerDb(db, "SQLite + outputs/", "NTFS", "Память и артефакты")
  Deployment_Node(job, "Sandbox: Job Object", "1 ГБ RAM, 256 процессов, 90 с, kill-on-close") {
    Container(chrome, "Chromium headless", "Playwright", "Рендер недоверенного HTML, своя песочница + блокировка сети")
  }
}
Rel(app, ollama, "HTTP :11434 (localhost)")
Rel(app, chrome, "Popen в Job Object")
Rel(app, db, "read/write")
```

## 9.3. Sequence Diagram — полный поток

```mermaid
sequenceDiagram
autonumber
actor U as Дизайнер
participant M as main.py
participant G as LangGraph
participant AD as Art Director
participant J as Jev-like (decide)
participant R as Router
participant UX as UX Researcher
participant GEN as UI Generator
participant V as Validator
participant T as Tools
participant S as Sandbox (Job Object)
participant O as Ollama

U->>M: python main.py "бриф"
M->>O: GET /api/tags (preflight)
M->>G: stream(state)
G->>AD: art_director(state)
AD->>J: is_ui_request, product_type, complexity
loop 3 голоса
  J->>O: chat(llama3.2, schema)
end
J-->>AD: значения + confidence
alt не UI и confidence ≥ 0.7
  AD-->>G: outcome = rejected
  G-->>M: END (шлюз 1)
else UI-запрос
  AD->>R: route_generator(complexity, type)
  R-->>AD: light / strong + причина
  AD->>O: chat(llama3.2, TASK_SCHEMA)
  AD-->>G: task, route, generator_model
  G->>UX: ux_researcher(state)
  UX->>O: chat(generator_model)
  UX-->>G: ux_notes
  loop попытка 1..3, пока fail
    G->>GEN: ui_generator(state)
    GEN->>R: escalate(model, attempt)
    GEN->>O: chat(model, HTML)
    GEN-->>G: html
    G->>V: validator(state)
    V->>R: pick_validator(generator_model)
    V->>T: check_contrast, lint_html
    V->>S: render (375 и 1280 px)
    S-->>V: overflow, ошибки JS, скриншоты
    V->>J: matches_brief, quality
    loop 5 голосов
      J->>O: chat(валидатор другого семейства)
    end
    alt есть ошибки инструментов
      V-->>G: fail + точные исправления
    else confidence < 0.7
      V-->>G: needs_human
    else quality ≥ 60 и соответствует брифу
      V-->>G: pass
    end
  end
  G-->>M: финальное состояние
end
M->>U: screen.html, report.json
```

## 9.4. Как архитектура закрывает требования ТЗ

| Требование | Где |
|---|---|
| Агенты объединены в одну систему, есть роутинг (не «несколько сингл-агентов») | Граф LangGraph, общее состояние, условные рёбра |
| Слой верификации и валидации | Validator: инструменты + LLM + пороги + `needs_human` |
| Валидатор роутит на LLM другого семейства | `router.pick_validator()`, тест `test_validator_is_always_other_family` |
| Роутинг на разные LLM | `router.route_generator()` + каскад `escalate()` |
| Jev-like модель | `decision_model.decide()` на входе и выходе |
| Изоляция | Windows Job Object + песочница Chromium (Deployment-диаграмма, раздел 4) |
