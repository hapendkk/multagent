"""Observability Pixel Crew: трейсы, структурные логи, метрики, алерты, траектории.

Только stdlib, локально. Формат спанов совместим по полям с OpenTelemetry
(trace_id, span_id, parent_id, name, kind, start, duration_ms, status, attributes),
поэтому JSONL можно перегнать в Phoenix/Langfuse/Jaeger без изменения кода агентов.

Файлы в outputs/telemetry/: traces.jsonl, logs.jsonl, metrics.jsonl, alerts.jsonl.
    python observability.py      # сводка по последним запускам (дашборд в терминале)
"""
import contextlib
import contextvars
import functools
import json
import os
import secrets
import statistics
import time

import config

_trace = contextvars.ContextVar("trace", default=None)
_parent = contextvars.ContextVar("parent", default=None)


class Trace:
    def __init__(self, run_id):
        self.run_id, self.trace_id = run_id, secrets.token_hex(16)
        self.spans, self.t0 = [], time.time()


def _write(name, record):
    if not config.TELEMETRY_ENABLED:
        return
    config.TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.TELEMETRY_DIR / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def start_trace(run_id):
    t = Trace(run_id)
    _trace.set(t)
    _parent.set(None)
    log("info", "run.start", run_id=run_id)
    return t


def current():
    return _trace.get()


def log(level, event, **fields):
    t = _trace.get()
    _write("logs.jsonl", {"ts": round(time.time(), 3), "level": level, "event": event,
                          "trace_id": t.trace_id if t else None, "span_id": _parent.get(), **fields})


@contextlib.contextmanager
def span(name, kind="internal", **attrs):
    """Спан: агент (agent), вызов LLM (llm), инструмент (tool), песочница (sandbox)."""
    t = _trace.get()
    if t is None:
        yield {}
        return
    sid, parent = secrets.token_hex(8), _parent.get()
    token = _parent.set(sid)
    rec = {"trace_id": t.trace_id, "span_id": sid, "parent_id": parent, "name": name, "kind": kind,
           "start": round(time.time(), 3), "status": "ok", "attributes": dict(attrs)}
    try:
        yield rec["attributes"]
    except Exception as e:
        rec["status"], rec["error"] = "error", f"{type(e).__name__}: {e}"
        log("error", f"{kind}.error", name=name, error=rec["error"])
        raise
    finally:
        _parent.reset(token)
        rec["duration_ms"] = round((time.time() - rec["start"]) * 1000)
        t.spans.append(rec)
        _write("traces.jsonl", rec)


def traced_node(name, fn):
    """Обёртка узла графа: спан агента + атрибуты из обновления состояния."""
    @functools.wraps(fn)
    def wrapper(state):
        with span(f"agent.{name}", "agent", attempt=state.get("attempt")) as a:
            upd = fn(state)
            for k in ("outcome", "generator_model", "attempt"):
                if k in upd:
                    a[k] = upd[k]
            if "verdict" in upd:
                v = upd["verdict"]
                a["quality"] = (v.get("quality") or {}).get("value")
                a["contrast_failures"] = len(v.get("contrast_failures", []))
                a["lint_errors"] = len(v.get("lint_errors", []))
            return upd
    return wrapper


def metrics(trace, final):
    sp = trace.spans
    llm = [s for s in sp if s["kind"] == "llm"]
    by_model = {}
    for s in llm:
        a = s["attributes"]
        m = by_model.setdefault(a.get("model"), {"calls": 0, "errors": 0, "ms": 0, "tokens_in": 0, "tokens_out": 0})
        m["calls"] += 1
        m["errors"] += s["status"] != "ok"
        m["ms"] += s["duration_ms"]
        m["tokens_in"] += a.get("tokens_in", 0)
        m["tokens_out"] += a.get("tokens_out", 0)
    for m in by_model.values():
        m["tokens_per_s"] = round(m["tokens_out"] / (m["ms"] / 1000), 1) if m["ms"] else None
    agents = {}
    for s in sp:
        if s["kind"] == "agent":
            agents[s["name"]] = agents.get(s["name"], 0) + s["duration_ms"]
    render = [s for s in sp if s["kind"] == "sandbox"]
    return {
        "ts": round(time.time(), 3), "run_id": trace.run_id, "trace_id": trace.trace_id,
        "outcome": final.get("outcome"), "attempts": final.get("attempt"),
        "run_seconds": round(time.time() - trace.t0, 1),
        "llm_calls": len(llm), "llm_errors": sum(s["status"] != "ok" for s in llm),
        "tokens_in": sum(m["tokens_in"] for m in by_model.values()),
        "tokens_out": sum(m["tokens_out"] for m in by_model.values()),
        "by_model": by_model, "agent_ms": agents,
        "tool_calls": sum(s["kind"] == "tool" for s in sp),
        "truncated": sum(s["attributes"].get("done_reason") == "length" for s in llm),
        "render_ms": sum(s["duration_ms"] for s in render),
        "sandbox_available": all(s["attributes"].get("available") for s in render) if render else None,
        "generator_model": final.get("generator_model"),
    }


def check_alerts(m, history=()):
    """Правила алертов. history — метрики прошлых запусков (для скользящих правил)."""
    A, out = config.ALERTS, []
    if m["run_seconds"] > A["run_seconds_max"]:
        out.append(("warning", "slow_run", f"запуск {m['run_seconds']} с > {A['run_seconds_max']} с"))
    if m["llm_errors"]:
        out.append(("critical", "llm_errors", f"ошибок LLM: {m['llm_errors']}"))
    if m["sandbox_available"] is False:
        out.append(("critical", "sandbox_down", "песочница недоступна, рендер не выполнен (fail-closed)"))
    if m["outcome"] == "needs_human":
        out.append(("warning", "needs_human", "низкая уверенность валидатора, нужен человек"))
    if m["outcome"] == "fail":
        out.append(("warning", "run_failed", f"FAIL после {m['attempts']} попыток"))
    if m.get("truncated"):
        out.append(("warning", "truncated_output", f"обрезанных ответов LLM: {m['truncated']}"))
    for model, s in m["by_model"].items():
        if s["tokens_per_s"] is not None and s["tokens_out"] > 50 and s["tokens_per_s"] < A["llm_tokens_per_s_min"]:
            out.append(("warning", "slow_model", f"{model}: {s['tokens_per_s']} ток/с, возможно, не на GPU"))
    window = [h for h in list(history)[-(A["rolling_window"] - 1):] if h.get("outcome") != "rejected"]
    if m["outcome"] != "rejected":
        window = window + [m]
    if len(window) >= 5:
        rate = sum(h["outcome"] == "pass" for h in window) / len(window)
        if rate < A["rolling_pass_rate_min"]:
            out.append(("critical", "pass_rate_drop", f"pass rate {rate:.0%} за {len(window)} запусков"))
    return [{"severity": s, "rule": r, "message": msg, "run_id": m["run_id"], "trace_id": m["trace_id"]}
            for s, r, msg in out]


def trajectory(trace):
    """Траектория агентов: упорядоченные шаги с вложенными LLM/tool/sandbox вызовами."""
    children = {}
    for s in trace.spans:
        children.setdefault(s["parent_id"], []).append(s)

    def node(s):
        return {"name": s["name"], "kind": s["kind"], "ms": s["duration_ms"], "status": s["status"],
                "attributes": s["attributes"],
                "children": [node(c) for c in sorted(children.get(s["span_id"], []), key=lambda x: x["start"])]}
    return [node(s) for s in sorted(children.get(None, []), key=lambda x: x["start"])]


def _history():
    p = config.TELEMETRY_DIR / "metrics.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def finish(final):
    t = _trace.get()
    if t is None:
        return {}
    m = metrics(t, final)
    alerts = check_alerts(m, _history())
    _write("metrics.jsonl", m)
    for a in alerts:
        _write("alerts.jsonl", {"ts": m["ts"], **a})
    log("info", "run.finish", outcome=m["outcome"], seconds=m["run_seconds"], alerts=len(alerts))
    return {"metrics": m, "alerts": alerts, "trajectory": trajectory(t)}


def dashboard(last=20):
    h = _history()[-last:]
    if not h:
        return "Телеметрии пока нет: запустите main.py."
    secs = sorted(x["run_seconds"] for x in h)
    lines = [f"Запусков: {len(h)}",
             f"Исходы: " + ", ".join(f"{k}={sum(x['outcome'] == k for x in h)}"
                                      for k in ("pass", "fail", "needs_human", "rejected")),
             f"Время, с: p50={statistics.median(secs)}, p95={secs[min(len(secs) - 1, int(0.95 * (len(secs) - 1) + 0.5))]}",
             f"Токены: вход {sum(x['tokens_in'] for x in h)}, выход {sum(x['tokens_out'] for x in h)}"]
    models = {}
    for x in h:
        for mod, s in x["by_model"].items():
            agg = models.setdefault(mod, [0, 0, 0])
            agg[0] += s["calls"]
            agg[1] += s["tokens_out"]
            agg[2] += s["ms"]
    for mod, (calls, tok, ms) in models.items():
        lines.append(f"  {mod}: вызовов {calls}, {round(tok / (ms / 1000), 1) if ms else '—'} ток/с")
    p = config.TELEMETRY_DIR / "alerts.jsonl"
    if p.exists():
        al = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()[-5:] if x.strip()]
        lines.append("Последние алерты:")
        lines += [f"  [{a['severity']}] {a['rule']}: {a['message']} ({a['run_id']})" for a in al]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(dashboard(int(os.environ.get("LAST", "20"))))
