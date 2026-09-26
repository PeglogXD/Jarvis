import json
import threading
import urllib.error
import urllib.request

from .config import PROVIDERS, get_api_key

TIMEOUT = 180


class LLMError(Exception):
    pass


def _post(url, headers, body, timeout=TIMEOUT, stream=False):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:900]
        except Exception:
            pass
        raise LLMError(f"HTTP {e.code} {e.reason}: {detail}")
    except urllib.error.URLError as e:
        raise LLMError(f"connection failed: {e.reason}")
    except Exception as e:
        raise LLMError(f"{type(e).__name__}: {e}")


def _headers(provider, cfg, stream=False):
    spec = PROVIDERS.get(provider, {})
    key = get_api_key(provider, cfg)
    h = {}
    if spec.get("header") == "x-goog-api-key":
        h["x-goog-api-key"] = key
    else:
        h["Authorization"] = f"Bearer {key}"
    if provider == "openrouter":
        h["HTTP-Referer"] = "https://opencode.ai"
        h["X-Title"] = "opencode-web"
    if stream:
        h["Accept"] = "text/event-stream"
    return h


def chat_stream(provider, cfg, messages, tools, model=None, temperature=None,
                max_tokens=14000, on_delta=None, on_think=None, signal=None):
    spec = PROVIDERS.get(provider)
    if not spec:
        raise LLMError(f"unknown provider {provider}")
    model = model or spec["default"]
    url = spec["base"].rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": cfg.get("temperature", 0.1) if temperature is None else temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        body["tools"] = tools
    resp = _post(url, _headers(provider, cfg, stream=True), body, TIMEOUT, stream=True)
    tool_acc = {}
    finish = None
    content_parts = []
    think_parts = []
    usage = None
    try:
        for raw in resp:
            if signal is not None and signal.is_set():
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    if on_delta:
                        on_delta(delta["content"])
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    think_parts.append(reasoning)
                    if on_think:
                        on_think(reasoning)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                    fn = tc.get("function") or {}
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    finally:
        try:
            resp.close()
        except Exception:
            pass
    tool_calls = []
    for idx in sorted(tool_acc):
        slot = tool_acc[idx]
        raw_args = slot["args"] or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        tool_calls.append({
            "id": slot["id"] or f"call_{idx}",
            "type": "function",
            "function": {"name": slot["name"], "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return {
        "content": "".join(content_parts),
        "reasoning": "".join(think_parts),
        "tool_calls": tool_calls,
        "finish_reason": finish or "stop",
        "usage": usage,
        "model": model,
    }


def complete(provider, cfg, messages, model=None, max_tokens=2000, temperature=0.2):
    out = {"content": ""}
    res = chat_stream(
        provider, cfg, messages, None, model=model,
        temperature=temperature, max_tokens=max_tokens,
        on_delta=lambda d: out.__setitem__("content", out["content"] + d),
    )
    return res


def list_models(provider, cfg):
    spec = PROVIDERS.get(provider)
    if not spec:
        return []
    try:
        url = spec["base"].rstrip("/") + "/models"
        req = urllib.request.Request(url, method="GET")
        for k, v in _headers(provider, cfg).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        if ids:
            return sorted(set(ids + spec["models"]))
    except Exception:
        pass
    return list(spec.get("models", []))
