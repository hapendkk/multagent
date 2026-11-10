"""Запуск Pixel Crew из терминала.

    python main.py "бриф на естественном языке"
"""
import argparse
import json
import sys
import time
from datetime import datetime

import config
import llm
import memory
import observability as obs
from langgraph.checkpoint.memory import InMemorySaver
import router
from graph import build_graph

DEFAULT_BRIEF = ("Мобильное приложение для медитации: главный экран с выбором "
                 "длительности сессии. Аудитория: люди 25-35 лет. Спокойный тон.")

MESSAGES = {
    "pass": "Готово: проверка пройдена.",
    "fail": "Проверка не пройдена за {n} попытки. Сохранена последняя версия, смотри замечания в report.json.",
    "needs_human": "Валидатор не уверен в оценке: нужен взгляд человека. Версия сохранена.",
    "rejected": "Бриф отклонён: это не похоже на запрос дизайна интерфейса.",
}


def preflight():
    need = {config.ROUTER_MODEL, config.GENERATOR_LIGHT,
            config.GENERATOR_STRONG, router.pick_validator(config.GENERATOR_LIGHT),
            router.pick_validator(config.GENERATOR_STRONG)}
    have = llm.list_models()
    missing = [m for m in need if m not in have and f"{m}:latest" not in have]
    if missing:
        print("Не хватает моделей:", ", ".join(missing))
        for m in missing:
            print(f"  ollama pull {m}")
        sys.exit(1)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Pixel Crew: мультиагентная дизайн-студия")
    ap.add_argument("brief", nargs="?", default=DEFAULT_BRIEF, help="бриф (в кавычках)")
    args = ap.parse_args()

    try:
        preflight()
    except llm.LLMError as e:
        print(e)
        sys.exit(1)

    print(f"Бриф: {args.brief}\n")
    print(f"Модели: router={config.ROUTER_MODEL}, generator={config.GENERATOR_LIGHT} / "
          f"{config.GENERATOR_STRONG}, validator: другое семейство из {config.VALIDATOR_CANDIDATES}\n")

    app = build_graph(checkpointer=InMemorySaver())   # L1: рабочая память с чекпоинтами
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    feedbacks = []
    obs.start_trace(run_id)
    final = {"brief": args.brief, "task": {}, "route": {}, "generator_model": "",
             "ux_notes": "", "html": "", "feedback": "", "verdict": {},
             "attempt": 0, "outcome": "running", "log": []}
    t0 = time.time()
    try:
        for chunk in app.stream(dict(final), config={"recursion_limit": 40, "configurable": {"thread_id": run_id}},
                                stream_mode="updates"):
            for node, upd in chunk.items():
                if not upd:
                    continue
                for line in upd.get("log", []):
                    print(line, flush=True)
                if node == "validator":
                    feedbacks.append(upd.get("feedback", ""))
                for k, v in upd.items():
                    if k == "log":
                        final["log"] = final["log"] + v
                    else:
                        final[k] = v
    except llm.LLMError as e:
        print(f"\nОшибка модели: {e}")
        sys.exit(1)

    total = round(time.time() - t0, 1)
    final["memory"] = memory.record_run(final, feedbacks)   # L2 эпизод + L3 уроки
    tel = obs.finish(final)
    final["telemetry"] = tel.get("metrics", {})
    final["alerts"] = tel.get("alerts", [])
    out_dir = config.OUTPUT_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if final["html"]:
        (out_dir / "screen.html").write_text(final["html"], encoding="utf-8")
    final["total_seconds"] = total
    (out_dir / "trajectory.json").write_text(
        json.dumps(tel.get("trajectory", []), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print(MESSAGES[final["outcome"]].format(n=config.MAX_ATTEMPTS))
    print(f"Время: {total} с. Результат: {out_dir}")
    m = final["telemetry"]
    if m:
        print(f"LLM: {m['llm_calls']} вызовов, токенов {m['tokens_in']}→{m['tokens_out']}, "
              f"инструментов {m['tool_calls']}, рендер {m['render_ms']} мс")
    for a in final["alerts"]:
        print(f"АЛЕРТ [{a['severity']}] {a['rule']}: {a['message']}")
    if final["html"]:
        print(f"Открой в браузере: {out_dir / 'screen.html'}")


if __name__ == "__main__":
    main()
