import json
import os
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
SESSIONS_DIR = APP_DIR / "sessions"
SNAPSHOT_DIR = APP_DIR / ".snapshots"

PROVIDERS = {
    "nvidia": {
        "label": "NVIDIA NIM",
        "base": "https://integrate.api.nvidia.com/v1",
        "keyEnv": "NVIDIA_API_KEY",
        "models": [
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "meta/llama-3.2-90b-vision-instruct",
            "qwen/qwen3-coder-480b-a35b-instruct",
            "deepseek-ai/deepseek-v4.1-flash",
            "mistralai/mistral-large-2-instruct",
        ],
        "default": "nvidia/nemotron-3-super-120b-a12b",
    },
    "gemini": {
        "label": "Google Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "keyEnv": "GEMINI_API_KEY",
        "header": "x-goog-api-key",
        "models": [
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
        ],
        "default": "gemini-3.5-flash",
    },
    "openai": {
        "label": "OpenAI",
        "base": "https://api.openai.com/v1",
        "keyEnv": "OPENAI_API_KEY",
        "models": ["gpt-5.1", "gpt-5.1-mini", "gpt-5", "gpt-4.1", "gpt-4o-mini"],
        "default": "gpt-5.1",
    },
    "anthropic": {
        "label": "Anthropic",
        "base": "https://api.anthropic.com/v1",
        "keyEnv": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"],
        "default": "claude-sonnet-5",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "keyEnv": "OPENROUTER_API_KEY",
        "models": [
            "anthropic/claude-sonnet-5",
            "openai/gpt-5.1",
            "google/gemini-3.5-flash",
            "z-ai/glm-5",
        ],
        "default": "anthropic/claude-sonnet-5",
    },
    "ollama": {
        "label": "Ollama (local)",
        "base": "http://127.0.0.1:11434/v1",
        "keyEnv": "OLLAMA_API_KEY",
        "local": True,
        "models": ["qwen3-coder:30b", "llama3.3:70b", "deepseek-r1:32b", "qwen2.5-coder:7b"],
        "default": "qwen3-coder:30b",
    },
    "zen": {
        "label": "OpenCode Zen",
        "base": "https://opencode.ai/zen/v1",
        "keyEnv": "OPENCODE_API_KEY",
        "models": ["big-pickle", "grok-code-fast-1", "qwen3-coder-480b"],
        "default": "big-pickle",
    },
}

DEFAULT_CONFIG = {
    "provider": "nvidia",
    "model": "nvidia/nemotron-3-super-120b-a12b",
    "workspace": str(APP_DIR),
    "apiKeys": {},
    "permissions": {
        "read": "allow",
        "edit": "ask",
        "bash": "ask",
        "web": "allow",
    },
    "mode": "build",
    "maxSteps": 40,
    "temperature": 0.1,
    "systemPrompt": "",
    "theme": "opencode",
}

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".env",
    "sessions", ".snapshots", "dist", "build", ".next", ".cache",
    ".mypy_cache", ".pytest_cache", "site-packages", ".idea", ".vscode",
    "opencode-replica", ".opencode",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".7z", ".tar", ".exe", ".dll", ".so", ".dylib", ".mp3", ".mp4", ".wav",
    ".woff", ".woff2", ".ttf", ".otf", ".pyc", ".class", ".jar", ".bin",
    ".sqlite", ".db", ".onnx", ".pt", ".pth", ".safetensors",
}


def new_id(prefix=""):
    return prefix + uuid.uuid4().hex[:12]


def load_dotenv_file(path):
    out = {}
    p = Path(path)
    if not p.is_file():
        return out
    try:
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            if k and v:
                out[k] = v
    except Exception:
        pass
    return out


def env_pool():
    pool = {}
    for p in (APP_DIR / ".env", APP_DIR.parent / ".env", Path.home() / ".opencode.env"):
        pool.update(load_dotenv_file(p))
    pool.update({k: v for k, v in os.environ.items() if v})
    return pool


def get_api_key(provider, config):
    manual = (config.get("apiKeys") or {}).get(provider)
    if manual:
        return manual.strip()
    spec = PROVIDERS.get(provider, {})
    if spec.get("local"):
        return "ollama"
    key_env = spec.get("keyEnv")
    if not key_env:
        return ""
    return (env_pool().get(key_env) or "").strip()


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
                perms = DEFAULT_CONFIG["permissions"].copy()
                perms.update(data.get("permissions") or {})
                cfg["permissions"] = perms
        except Exception:
            pass
    if not cfg.get("model") or not PROVIDERS.get(cfg.get("provider")):
        cfg["provider"] = DEFAULT_CONFIG["provider"]
        cfg["model"] = PROVIDERS[DEFAULT_CONFIG["provider"]]["default"]
    return cfg


def save_config(cfg):
    allowed = {k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(allowed, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    return allowed


def public_config(cfg):
    providers = []
    for pid, spec in PROVIDERS.items():
        providers.append({
            "id": pid,
            "label": spec["label"],
            "models": spec["models"],
            "default": spec["default"],
            "hasKey": bool(get_api_key(pid, cfg)),
            "local": bool(spec.get("local")),
            "active": pid == cfg.get("provider"),
        })
    active = cfg.get("provider")
    return {
        "provider": active,
        "model": cfg.get("model"),
        "workspace": cfg.get("workspace"),
        "permissions": cfg.get("permissions"),
        "mode": cfg.get("mode"),
        "maxSteps": cfg.get("maxSteps"),
        "temperature": cfg.get("temperature"),
        "providers": providers,
        "version": "2.0.0",
    }
