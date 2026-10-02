"""J.A.R.V.I.S — Backend de IA para la nube (Opción A).

La ventana de Jarvis sigue viviendo en tu PC (jarvis.py). Este servidor solo
"piensa": recibe el prompt y devuelve la respuesta usando Gemini / NVIDIA /
Ollama en cascada. Se puede desplegar gratis en Render, Fly.io, Cloud Run, etc.

Endpoints:
  GET  /health        → estado y proveedores disponibles
  POST /chat          → {"text": "..."} (no streaming)
  POST /chat/stream   → texto en vivo (text/plain chunked)

Auth: cabecera X-Jarvis-Token = JARVIS_API_TOKEN (si defines la variable).
"""
import os
import hmac
import time
import json

from flask import Flask, request, Response, jsonify

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
OLLAMA_HOST = (os.getenv("OLLAMA_HOST") or "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
API_TOKEN = os.getenv("JARVIS_API_TOKEN", "") or ""
DEFAULT_ORDER = [p.strip() for p in os.getenv("IA_PROVIDERS", "gemini,nvidia,ollama").split(",") if p.strip()]

app = Flask(__name__)

_gemini_client = None
_nvidia_client = None
_ollama_client = None


def _gemini():
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _nvidia():
    global _nvidia_client
    if _nvidia_client is None and NVIDIA_API_KEY:
        import openai
        _nvidia_client = openai.OpenAI(
            api_key=NVIDIA_API_KEY, base_url="https://integrate.api.nvidia.com/v1")
    return _nvidia_client


def _ollama():
    global _ollama_client
    if _ollama_client is None and OLLAMA_HOST:
        import openai
        _ollama_client = openai.OpenAI(api_key="ollama", base_url=OLLAMA_HOST + "/v1", timeout=120)
    return _ollama_client


def _autorizado():
    if not API_TOKEN:
        return True
    recibido = request.headers.get("X-Jarvis-Token", "")
    return hmac.compare_digest(recibido, API_TOKEN)


def _mensajes(data):
    msgs = data.get("messages")
    if isinstance(msgs, list) and msgs:
        out = []
        for m in msgs:
            if isinstance(m, dict) and m.get("content"):
                out.append({"role": m.get("role", "user"), "content": m.get("content")})
        return out
    out = []
    system = data.get("system")
    if system:
        out.append({"role": "system", "content": system})
    out.append({"role": "user", "content": data.get("prompt") or ""})
    return out


def _gemini_stream(messages, max_tokens):
    from google import genai
    client = _gemini()
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    contents = []
    for m in messages:
        if m.get("role") == "system":
            continue
        role = "user" if m.get("role") == "user" else "model"
        contents.append(genai.types.Content(role=role, parts=[genai.types.Part.from_text(text=m["content"])]))
    cfg = {"max_output_tokens": max_tokens}
    if system:
        cfg["system_instruction"] = system
    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=genai.types.GenerateContentConfig(**cfg),
        history=contents[:-1] or None,
    )
    ultimo = contents[-1].parts[0].text if contents and contents[-1].parts else ""
    stream = chat.send_message_stream(ultimo)
    for chunk in stream:
        txt = getattr(chunk, "text", None)
        if txt:
            yield txt


def _openai_stream(client, model, messages, max_tokens, extra_body=None):
    kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, stream=True)
    if extra_body:
        kwargs["extra_body"] = extra_body
    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        if chunk.choices:
            t = chunk.choices[0].delta.content or ""
            if t:
                yield t


def _stream(proveedor, messages, max_tokens):
    if proveedor == "gemini":
        if not _gemini():
            raise RuntimeError("Gemini no configurado")
        yield from _gemini_stream(messages, max_tokens)
    elif proveedor == "nvidia":
        if not _nvidia():
            raise RuntimeError("NVIDIA no configurado")
        yield from _openai_stream(_nvidia(), NVIDIA_MODEL, messages, max_tokens,
                                  {"chat_template_kwargs": {"enable_thinking": False}})
    elif proveedor == "ollama":
        if not _ollama():
            raise RuntimeError("Ollama no configurado")
        yield from _openai_stream(_ollama(), OLLAMA_MODEL, messages, max_tokens)
    else:
        raise RuntimeError("proveedor desconocido: %s" % proveedor)


def _orden(data):
    pedido = data.get("providers")
    if isinstance(pedido, list) and pedido:
        return [str(p).strip() for p in pedido if str(p).strip()]
    return list(DEFAULT_ORDER)


@app.get("/health")
def health():
    if not _autorizado():
        return jsonify({"error": "no autorizado"}), 401
    disponibles = []
    try:
        if _gemini():
            disponibles.append("gemini")
    except Exception:
        pass
    try:
        if _nvidia():
            disponibles.append("nvidia")
    except Exception:
        pass
    try:
        if _ollama():
            disponibles.append("ollama")
    except Exception:
        pass
    return jsonify({"ok": True, "providers": disponibles, "orden": DEFAULT_ORDER,
                    "modelos": {"gemini": GEMINI_MODEL, "nvidia": NVIDIA_MODEL, "ollama": OLLAMA_MODEL}})


@app.post("/chat")
def chat():
    if not _autorizado():
        return jsonify({"error": "no autorizado"}), 401
    data = request.get_json(force=True, silent=True) or {}
    messages = _mensajes(data)
    max_tokens = int(data.get("max_tokens") or 1024)
    errores = []
    for prov in _orden(data):
        try:
            texto = "".join(_stream(prov, messages, max_tokens))
            if texto.strip():
                return jsonify({"text": texto, "provider": prov})
            errores.append(f"{prov}: respuesta vacía")
        except Exception as e:
            errores.append(f"{prov}: {e}")
    return jsonify({"error": "ningún proveedor respondió", "detalles": errores}), 502


@app.post("/chat/stream")
def chat_stream():
    if not _autorizado():
        return jsonify({"error": "no autorizado"}), 401
    data = request.get_json(force=True, silent=True) or {}
    messages = _mensajes(data)
    max_tokens = int(data.get("max_tokens") or 1024)
    orden = _orden(data)

    def generar():
        errores = []
        for prov in orden:
            emitido = False
            try:
                for tok in _stream(prov, messages, max_tokens):
                    emitido = True
                    yield tok
            except Exception as e:
                errores.append(f"{prov}: {e}")
                continue
            if emitido:
                return
            errores.append(f"{prov}: respuesta vacía")
        yield "\n[Servidor Jarvis] Ningún proveedor respondió. " + "; ".join(errores)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return Response(generar(), mimetype="text/plain; charset=utf-8", headers=headers)


if __name__ == "__main__":
    puerto = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=puerto, threaded=True)
