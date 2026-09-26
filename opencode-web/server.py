import json
import mimetypes
import os
import posixpath
import queue
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config as cfgmod
from core import llm, tools
from core.agent import Agent
from core.session import (
    Aborted, RunState, Session, delete_session, ensure_dirs, get_session,
    list_sessions,
)

WEB_DIR = Path(__file__).resolve().parent / "web"
RUNS = {}
RUNS_LOCK = threading.Lock()
HOST = os.environ.get("OPENCODE_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENCODE_WEB_PORT", "7791"))


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def register_run(sid, state):
    with RUNS_LOCK:
        RUNS[sid] = state
    threading.Timer(3600, lambda: unregister_run(sid)).start()


def unregister_run(sid):
    with RUNS_LOCK:
        RUNS.pop(sid, None)


def get_run(sid):
    with RUNS_LOCK:
        return RUNS.get(sid)


class Handler(BaseHTTPRequestHandler):
    server_version = "opencode-web/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("OPENCODE_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json_bytes(obj))

    def _err(self, msg, code=400):
        self._json({"error": str(msg)}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                return self.api_get(path, qs)
            return self.serve_static(path)
        except Exception as e:
            traceback.print_exc()
            self._err(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            return self.api_post(parsed.path, self._body())
        except Exception as e:
            traceback.print_exc()
            self._err(f"{type(e).__name__}: {e}", 500)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/session":
                sid = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
                return self._json({"deleted": delete_session(sid)})
            return self._err("not found", 404)
        except Exception as e:
            self._err(f"{type(e).__name__}: {e}", 500)

    def api_get(self, path, qs):
        if path == "/api/config":
            cfg = cfgmod.load_config()
            return self._json(cfgmod.public_config(cfg))
        if path == "/api/models":
            provider = qs.get("provider", [""])[0] or cfgmod.load_config()["provider"]
            cfg = cfgmod.load_config()
            return self._json({
                "provider": provider,
                "models": llm.list_models(provider, cfg),
                "hasKey": bool(cfgmod.get_api_key(provider, cfg)),
            })
        if path == "/api/tree":
            cfg = cfgmod.load_config()
            root = Path(cfg.get("workspace") or ".").resolve()
            sub = qs.get("path", [None])[0]
            if not root.is_dir():
                return self._json({"root": str(root), "tree": []})
            try:
                base = tools.safe_path(root, sub) if sub else root
            except tools.ToolError as e:
                return self._err(str(e), 400)
            tree = []
            if base.is_file():
                return self._json({"root": str(root), "tree": []})
            try:
                entries = sorted(
                    base.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
                )
            except Exception as e:
                return self._err(f"cannot read directory: {e}", 400)
            for e in entries:
                if e.name in tools.IGNORE_DIRS:
                    continue
                if e.name.startswith(".") and e.name not in (".env", ".github"):
                    continue
                if e.is_symlink():
                    continue
                is_dir = e.is_dir()
                tree.append({
                    "name": e.name,
                    "path": tools.rel_path(root, e),
                    "type": "dir" if is_dir else "file",
                    "size": 0 if is_dir else e.stat().st_size,
                })
                if len(tree) > 800:
                    break
            return self._json({"root": str(root), "path": sub or "", "tree": tree})
        if path == "/api/file":
            cfg = cfgmod.load_config()
            root = Path(cfg.get("workspace") or ".").resolve()
            rel = qs.get("path", [""])[0]
            try:
                p = tools.safe_path(root, rel)
            except tools.ToolError as e:
                return self._err(str(e), 400)
            if not p.is_file():
                return self._err("not found", 404)
            if p.suffix.lower() in tools.BINARY_EXT:
                return self._json({"path": rel, "binary": True, "size": p.stat().st_size})
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return self._err(str(e), 400)
            return self._json({
                "path": rel, "content": text[:1_500_000],
                "lines": text.count("\n") + 1, "size": p.stat().st_size,
            })
        if path == "/api/sessions":
            cfg = cfgmod.load_config()
            return self._json({"sessions": list_sessions(cfg.get("workspace"))})
        if path == "/api/session":
            sid = qs.get("id", [""])[0]
            cfg = cfgmod.load_config()
            s = get_session(sid, cfg.get("workspace"))
            if not s:
                return self._err("session not found", 404)
            return self._json(s.to_dict())
        if path == "/api/health":
            cfg = cfgmod.load_config()
            prov = cfg.get("provider")
            return self._json({
                "ok": True,
                "provider": prov,
                "model": cfg.get("model"),
                "hasKey": bool(cfgmod.get_api_key(prov, cfg)),
                "workspace": cfg.get("workspace"),
            })
        return self._err("not found", 404)

    def api_post(self, path, body):
        if path == "/api/config":
            cfg = cfgmod.load_config()
            incoming = body or {}
            for k in ("provider", "model", "workspace", "mode", "maxSteps",
                      "temperature", "systemPrompt"):
                if k in incoming:
                    cfg[k] = incoming[k]
            if "apiKeys" in incoming and isinstance(incoming["apiKeys"], dict):
                keys = dict(cfg.get("apiKeys") or {})
                for k, v in incoming["apiKeys"].items():
                    if v:
                        keys[k] = v
                cfg["apiKeys"] = keys
            if "permissions" in incoming and isinstance(incoming["permissions"], dict):
                perms = dict(cfg.get("permissions") or {})
                perms.update(incoming["permissions"])
                cfg["permissions"] = perms
            if "workspace" in incoming and incoming["workspace"]:
                wp = Path(incoming["workspace"]).expanduser()
                if not wp.is_dir():
                    return self._err(f"workspace not found: {incoming['workspace']}", 400)
                cfg["workspace"] = str(wp.resolve())
            saved = cfgmod.save_config(cfg)
            return self._json(cfgmod.public_config(cfgmod.load_config()))
        if path == "/api/session/new":
            cfg = cfgmod.load_config()
            s = Session(root=cfg.get("workspace"))
            s.mode = body.get("mode") or cfg.get("mode", "build")
            s.provider = cfg.get("provider")
            s.model = cfg.get("model")
            s.save()
            return self._json(s.to_dict())
        if path == "/api/permission":
            sid = body.get("session")
            run = get_run(sid)
            if not run:
                return self._err("no active run", 404)
            run.resolve({
                "allowed": bool(body.get("allowed")),
                "remember": bool(body.get("remember")),
            })
            return self._json({"ok": True})
        if path == "/api/abort":
            sid = body.get("session")
            run = get_run(sid)
            if run:
                run.abort.set()
                if run.pending:
                    run.resolve({"allowed": False, "reason": "aborted"})
            return self._json({"ok": True, "aborted": bool(run)})
        if path == "/api/chat":
            return self.stream_chat(body)
        return self._err("not found", 404)

    def stream_chat(self, body):
        cfg = cfgmod.load_config()
        sid = body.get("session")
        s = get_session(sid, cfg.get("workspace")) if sid else None
        if not s:
            s = Session(root=cfg.get("workspace"))
            s.provider = cfg.get("provider")
            s.model = cfg.get("model")
        if body.get("mode") in ("plan", "build"):
            s.mode = body["mode"]
        if body.get("provider"):
            cfg["provider"] = body["provider"]
        if body.get("model"):
            cfg["model"] = body["model"]
            s.model = body["model"]
            s.provider = cfg["provider"]
        s.save()

        state = RunState(s)
        s.state = state
        register_run(s.id, state)
        q = queue.Queue(maxsize=2000)

        def emit(ev, data):
            try:
                q.put_nowait((ev, data))
            except queue.Full:
                pass

        def worker():
            try:
                Agent(s, cfg, emit, state).run(
                    user_text=(body.get("text") or "").strip() or None,
                    command=(body.get("command") or "").strip() or None,
                )
            except Aborted:
                emit("aborted", {"session": s.id})
            except Exception as e:
                emit("error", {"message": f"{type(e).__name__}: {e}",
                               "trace": traceback.format_exc()[-1200:]})
            finally:
                emit("__end__", {})
                unregister_run(s.id)

        threading.Thread(target=worker, daemon=True).start()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def w(ev, data):
            chunk = f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()

        try:
            w("open", {"session": s.id, "mode": s.mode,
                       "provider": s.provider, "model": s.model})
            last_ping = time_now()
            while True:
                try:
                    ev, data = q.get(timeout=10)
                except queue.Empty:
                    if time_now() - last_ping > 8:
                        last_ping = time_now()
                        try:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                        except Exception:
                            break
                    continue
                if ev == "__end__":
                    w("done", data)
                    break
                w(ev, data)
        except (BrokenPipeError, ConnectionResetError):
            state.abort.set()
        except Exception:
            state.abort.set()
            traceback.print_exc()
        finally:
            unregister_run(s.id)

    def serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        clean = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        target = (WEB_DIR / clean).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._err("forbidden", 403)
        if not target.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or "javascript" in ctype or "json" in ctype:
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        self._send(200, data, ctype)


def time_now():
    import time
    return time.time()


def main():
    ensure_dirs()
    cfg = cfgmod.load_config()
    prov = cfg.get("provider")
    has = bool(cfgmod.get_api_key(prov, cfg))
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    lan = HOST not in ("127.0.0.1", "localhost")
    print("=" * 62)
    print("  opencode-web 2.0  —  the open source AI coding agent")
    print("=" * 62)
    print(f"  URL       http://{HOST}:{PORT}")
    print(f"  workspace {cfg.get('workspace')}")
    print(f"  provider  {prov} / {cfg.get('model')}")
    print(f"  api key   {'found' if has else 'MISSING — add it in the UI'}")
    if lan:
        print("-" * 62)
        print("  WARNING: listening on all interfaces.")
        print("  Anyone on this network can run tools in your workspace.")
        print("  Set OPENCODE_WEB_HOST=127.0.0.1 to restrict to this machine.")
    print("=" * 62)
    print("  Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
