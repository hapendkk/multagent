"""Тонкая обёртка над HTTP API Ollama (без сторонних библиотек)."""
import json
import re
import time
import urllib.error
import urllib.request

import config
import observability as obs


class LLMError(RuntimeError):
    pass


# Модели, которые не принимают параметр think (запоминаем, чтобы не повторять ошибку)
_NO_THINK = set()


def _post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_HOST + path, data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_models():
    """Названия скачанных моделей."""
    try:
        with urllib.request.urlopen(config.OLLAMA_HOST + "/api/tags", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise LLMError(
            "Не удаётся подключиться к Ollama (localhost:11434). "
            "Запусти Ollama из меню «Пуск» и повтори.") from e
    return {m["name"] for m in data.get("models", [])}


def parse_json(text):
    """Достаёт JSON-объект из ответа модели. None, если не получилось."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


def chat(model, system, user, schema=None, temperature=0.2,
         max_tokens=800, num_ctx=None):
    """Один запрос к модели.

    schema: JSON-схема ответа (или строка "json"). Если задана, Ollama заставляет
    модель выдать только валидный JSON (structured output).
    """
    options = {"temperature": temperature, "num_predict": max_tokens}
    if num_ctx:
        options["num_ctx"] = num_ctx
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": options,
        "keep_alive": "10m",
    }
    if schema:
        payload["format"] = schema
    if model not in _NO_THINK:
        payload["think"] = False   # отключаем «размышления»: они съедают лимит токенов

    t0 = time.time()
    r = _call(payload, model)

    msg = r.get("message", {})
    text = (msg.get("content") or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return {"text": text, "seconds": round(time.time() - t0, 1),
            "tokens": r.get("eval_count", 0),
            "done_reason": r.get("done_reason"),
            "had_thinking": bool(msg.get("thinking"))}


def _call(payload, model):
    with obs.span("llm.chat", "llm", model=model, tools=bool(payload.get("tools")),
                  schema=bool(payload.get("format"))) as a:
        r = _call_raw(payload, model)
        a["tokens_in"], a["tokens_out"] = r.get("prompt_eval_count", 0), r.get("eval_count", 0)
        a["tool_calls"] = len((r.get("message") or {}).get("tool_calls") or [])
        a["done_reason"] = r.get("done_reason")
        return r


def _call_raw(payload, model):
    """POST /api/chat c обработкой ошибок и моделей без параметра think."""
    try:
        return _post("/api/chat", payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if "think" in payload and "think" in body.lower():
            _NO_THINK.add(model)
            payload.pop("think")
            try:
                return _post("/api/chat", payload)
            except urllib.error.HTTPError as e2:
                raise LLMError(f"{model}: HTTP {e2.code}") from e2
        if "tools" in payload and "tool" in body.lower():
            raise ToolsUnsupported(f"{model}: {body[:200]}") from e
        raise LLMError(f"{model}: HTTP {e.code}: {body[:200]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"{model}: нет связи с Ollama ({e.reason})") from e


class ToolsUnsupported(LLMError):
    pass


def chat_tools(model, system, user, tool_specs, executor, max_rounds=4,
               temperature=0.2, max_tokens=800, num_ctx=None):
    """Агентный цикл tool calling: модель вызывает инструменты, получает результаты
    и продолжает, пока не даст текстовый ответ или не кончатся раунды.

    executor(name, args) -> dict. Возвращает text, calls (траектория), rounds, seconds.
    """
    options = {"temperature": temperature, "num_predict": max_tokens}
    if num_ctx:
        options["num_ctx"] = num_ctx
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    calls, t0 = [], time.time()
    for rnd in range(1, max_rounds + 1):
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": options, "keep_alive": "10m"}
        last = rnd == max_rounds
        if not last:
            payload["tools"] = tool_specs
        else:
            messages.append({"role": "user", "content": "Лимит вызовов исчерпан. Дай финальный ответ."})
        if model not in _NO_THINK:
            payload["think"] = False
        try:
            r = _call(payload, model)
        except ToolsUnsupported:
            res = chat(model, system, user, temperature=temperature,
                       max_tokens=max_tokens, num_ctx=num_ctx)
            return {**res, "calls": calls, "rounds": rnd, "tools_supported": False}
        msg = r.get("message", {})
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
            return {"text": text, "calls": calls, "rounds": rnd, "tools_supported": True,
                    "seconds": round(time.time() - t0, 1)}
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments", {})
            if isinstance(args, str):
                args = parse_json(args) or {}
            result = executor(name, args)
            calls.append({"round": rnd, "name": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_name": name,
                             "content": json.dumps(result, ensure_ascii=False)})
    return {"text": "", "calls": calls, "rounds": max_rounds, "tools_supported": True,
            "seconds": round(time.time() - t0, 1)}
