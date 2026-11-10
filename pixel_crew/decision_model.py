"""Jev-like модель принятия решений (имитация принципа System One).

Идея настоящего Jev: модель не пишет текст, а возвращает значения заранее
заданных типов (выбор из набора, число, да/нет) вместе с уверенностью.

Как это сделано у нас:
  1. Структурированный вывод. Ollama заставляет модель ответить JSON строго по схеме
     (`format`), поэтому ответ не может выйти за допустимые значения.
  2. Уверенность = согласованность N независимых «голосов» (self-consistency):
     опрашиваем модель несколько раз и смотрим, как часто ответы совпадают.
  3. Эта уверенность пока НЕ откалибрована. Калибровку (совпадает ли «0.9» с реальной
     долей верных ответов) мы измерим позже в разделе Evals.

Отличие от настоящего Jev: он считает все ответы за один параллельный проход
за 70-500 мс, а у нас это несколько обычных вызовов LLM.
"""
from collections import Counter
from statistics import median
import json
import time

import config
import llm


def _schema(questions):
    props = {}
    for name, q in questions.items():
        t = q["type"]
        if t == "choice":
            props[name] = {"type": "string", "enum": q["options"]}
        elif t == "score":
            props[name] = {"type": "integer"}
        elif t == "bool":
            props[name] = {"type": "boolean"}
        else:
            raise ValueError(f"Неизвестный тип вопроса: {t}")
    return {"type": "object", "properties": props, "required": list(props)}


def _describe(questions):
    lines = []
    for name, q in questions.items():
        if q["type"] == "choice":
            lines.append(f'- "{name}": выбери одно из {q["options"]}. {q["ask"]}')
        elif q["type"] == "score":
            lines.append(f'- "{name}": целое число от {q["min"]} до {q["max"]}. {q["ask"]}')
        else:
            lines.append(f'- "{name}": true или false. {q["ask"]}')
    return "\n".join(lines)


def _example(questions):
    ex = {}
    for name, q in questions.items():
        if q["type"] == "choice":
            ex[name] = q["options"][0]
        elif q["type"] == "score":
            ex[name] = (q["min"] + q["max"]) // 2
        else:
            ex[name] = True
    return json.dumps(ex, ensure_ascii=False)


_MODES = ("schema", "json", "plain")   # от строгого режима к запасным


def decide(model, role, context, questions, n_votes=3, temperature=0.7):
    """Задаёт набор типизированных вопросов и возвращает решения с уверенностью.

    Возвращает словарь: {имя: {"value": ..., "confidence": 0..1}, "_meta": {...}}
    """
    system = (
        f"{role}\n\n"
        "Ты работаешь как модель принятия решений. Отвечай ТОЛЬКО JSON-объектом "
        "по заданной схеме, без пояснений. Вопросы:\n" + _describe(questions)
        + "\nПример формата ответа: " + _example(questions)
    )
    schema = _schema(questions)
    samples, problems = [], []
    t0 = time.time()
    start = 0                       # с какого режима начинать следующий голос
    used_mode = None
    for _ in range(n_votes):
        got = None
        for idx in range(start, len(_MODES)):
            mode = _MODES[idx]
            fmt = schema if mode == "schema" else ("json" if mode == "json" else None)
            try:
                r = llm.chat(model, system, context, schema=fmt,
                             temperature=temperature, max_tokens=300,
                             num_ctx=config.DECISION_NUM_CTX)
            except llm.LLMError as e:
                problems.append(f"{mode}: {e}")
                continue
            data = llm.parse_json(r["text"])
            if isinstance(data, dict):
                got, used_mode, start = data, mode, idx
                break
            problems.append(f"{mode}: пустой или нечитаемый ответ "
                            f"(done={r.get('done_reason')}, thinking={r.get('had_thinking')}, "
                            f"текст={r['text'][:50]!r})")
        if got is not None:
            samples.append(got)

    result = {}
    for name, q in questions.items():
        vals = [s[name] for s in samples if name in s]
        t = q["type"]
        if t == "choice":
            vals = [v for v in vals if v in q["options"]]
        elif t == "bool":
            vals = [v for v in vals if isinstance(v, bool)]
        elif t == "score":
            vals = [min(q["max"], max(q["min"], int(v)))
                    for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            result[name] = {"value": None, "confidence": 0.0}
            continue
        if t == "score":
            spread = max(vals) - min(vals)
            rng = q["max"] - q["min"]
            result[name] = {"value": int(median(vals)),
                            "confidence": round(max(0.0, 1 - spread / rng), 2)}
        else:
            value, count = Counter(vals).most_common(1)[0]
            result[name] = {"value": value, "confidence": round(count / len(vals), 2)}

    result["_meta"] = {"model": model, "votes_requested": n_votes,
                       "votes_ok": len(samples), "mode": used_mode,
                       "problems": list(dict.fromkeys(problems))[:4],
                       "seconds": round(time.time() - t0, 1)}
    return result
