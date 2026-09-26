import json
import threading
import time
from pathlib import Path

from .config import SESSIONS_DIR, SNAPSHOT_DIR, new_id

PLAN_DENY = {"write", "edit", "bash"}


def ensure_dirs():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _sdir():
    ensure_dirs()
    return SESSIONS_DIR


class RunState:
    def __init__(self, session):
        self.session = session
        self.abort = threading.Event()
        self.decision = threading.Event()
        self.pending = None
        self.answer = None
        self.lock = threading.Lock()
        self.step = 0
        self.busy = False
        self.started = time.time()

    def request_permission(self, payload, timeout=180):
        with self.lock:
            self.pending = payload
            self.decision.clear()
            self.answer = None
        if not self.decision.wait(timeout):
            with self.lock:
                self.pending = None
            return {"allowed": False, "reason": "permission timed out"}
        with self.lock:
            ans = self.answer
            self.pending = None
            self.answer = None
        return ans or {"allowed": False, "reason": "no response"}

    def resolve(self, payload):
        with self.lock:
            self.answer = payload
        self.decision.set()

    def check_abort(self):
        if self.abort.is_set():
            raise Aborted()
        self.step += 1
        return self.step


class Aborted(Exception):
    pass


class ToolContext:
    def __init__(self, root, session):
        self.root = Path(root).resolve()
        self.session = session
        self.todos = []
        self.snapshots = []

    def snapshot(self, rel, before, after):
        sid = self.session.id
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "id": new_id(),
            "path": rel,
            "before": before,
            "after": after,
            "ts": time.time(),
        }
        (SNAPSHOT_DIR / f"{sid}.jsonl").open("a", encoding="utf-8").write(
            json.dumps(rec, ensure_ascii=False) + "\n"
        )
        self.snapshots.append(rec)


class Session:
    def __init__(self, data=None, root=None):
        data = data or {}
        self.id = data.get("id") or new_id("s_")
        self.title = data.get("title") or "New session"
        self.created = data.get("created") or time.time()
        self.updated = self.created
        self.messages = data.get("messages") or []
        self.mode = data.get("mode") or "build"
        self.todos = data.get("todos") or []
        self.model = data.get("model") or ""
        self.provider = data.get("provider") or ""
        self.root = Path(root or ".").resolve()
        self.undo = data.get("undo") or []
        self.redo = data.get("redo") or []
        self.state = None

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "messages": self.messages,
            "mode": self.mode,
            "todos": self.todos,
            "model": self.model,
            "provider": self.provider,
            "undo": self.undo,
            "redo": self.redo,
        }

    def path(self):
        return _sdir() / f"{self.id}.json"

    def save(self):
        self.updated = time.time()
        p = self.path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return p

    def append(self, role, content, **extra):
        msg = {"role": role, "content": content, "ts": time.time()}
        msg.update(extra)
        self.messages.append(msg)
        return msg

    def _load_snapshots(self):
        if self.undo:
            return
        p = SNAPSHOT_DIR / f"{self.id}.jsonl"
        if not p.is_file():
            return
        loaded = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.undo.append(json.loads(line))
            except Exception:
                continue

    def undo_last(self):
        if not self.undo:
            self._load_snapshots()
        if not self.undo:
            return "nothing to undo"
        rec = self.undo[-1]
        target = self.root / rec["path"]
        before = rec["before"]
        try:
            if before is None:
                if target.exists():
                    target.unlink()
                    action = f"deleted {rec['path']} (was created)"
                else:
                    action = f"{rec['path']} already absent"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(before, encoding="utf-8")
                action = f"reverted {rec['path']} to previous content"
            self.undo.pop()
            self.redo.append(rec)
            return "undo: " + action
        except Exception as e:
            return f"undo failed: {type(e).__name__}: {e}"

    def redo_last(self):
        if not self.redo:
            if not self.undo:
                self._load_snapshots()
            return "nothing to redo"
        rec = self.redo[-1]
        target = self.root / rec["path"]
        after = rec.get("after")
        try:
            current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
            if after is None:
                if target.exists():
                    target.unlink()
                action = f"removed {rec['path']} again"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(after, encoding="utf-8")
                action = f"re-applied change to {rec['path']}"
            self.redo.pop()
            self.undo.append({**rec, "before": current})
            return "redo: " + action
        except Exception as e:
            return f"redo failed: {type(e).__name__}: {e}"


def list_sessions(root, limit=60):
    _sdir()
    out = []
    for f in _sdir().glob("s_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        msgs = d.get("messages") or []
        last = ""
        for m in reversed(msgs):
            if m.get("role") == "user" and m.get("content"):
                last = str(m["content"])[:80]
                break
        out.append({
            "id": d.get("id"),
            "title": d.get("title") or "New session",
            "updated": d.get("updated", 0),
            "mode": d.get("mode", "build"),
            "model": d.get("model", ""),
            "messages": len(msgs),
            "lastUser": last,
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out[:limit]


def get_session(sid, root):
    p = _sdir() / f"{sid}.json"
    if not p.is_file():
        return None
    try:
        return Session(json.loads(p.read_text(encoding="utf-8")), root)
    except Exception:
        return None


def delete_session(sid):
    p = _sdir() / f"{sid}.json"
    if p.is_file():
        p.unlink()
        return True
    return False
