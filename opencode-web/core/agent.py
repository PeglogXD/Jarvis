import json
import re
import time
from pathlib import Path

from . import llm, tools
from .config import get_api_key
from .session import Aborted, PLAN_DENY, ToolContext

BASE_SYSTEM = """You are {name}, an open source AI coding agent running in the user's workspace.

Operate like a senior engineer:
- Investigate before you change anything. Read the real files, don't guess their contents.
- Prefer {edit_tool} over write for changes to existing files; write is for new files or full rewrites.
- Keep changes minimal and consistent with the surrounding code style.
- Use bash to verify: run tests, linters, or git diff when relevant.
- Track multi-step work with todowrite. Exactly one todo should be in_progress at a time.
- Never invent file contents, APIs, or command output. If you did not run it, say so.

Mode: {mode}. {mode_note}

When you finish, summarize what you changed in 1-3 short sentences."""


class Agent:
    def __init__(self, session, cfg, emit, run_state):
        self.session = session
        self.cfg = cfg
        self.emit = emit
        self.state = run_state
        self.root = Path(cfg.get("workspace") or ".").resolve()
        self.ctx = ToolContext(self.root, session)
        self.ctx.todos = list(session.todos or [])

    def emit_todos(self):
        self.emit("todos", {"todos": self.ctx.todos})
        self.session.todos = self.ctx.todos

    def system_prompt(self):
        cfg = self.cfg
        mode = self.session.mode
        plan = mode == "plan"
        tool_names = "bash, glob, grep, ls, read, todowrite, webfetch, websearch"
        if not plan:
            tool_names += ", edit, write"
        mode_note = (
            "You are in PLAN mode: you may investigate with read/ls/glob/grep and bash, "
            "but writing, editing and destructive commands are BLOCKED. Produce a concrete, "
            "numbered implementation plan with the exact files you would touch. Do not "
            "pretend to have made changes."
            if plan else
            "You are in BUILD mode: you may edit files and run commands directly."
        )
        parts = [BASE_SYSTEM.format(
            name="opencode", edit_tool="edit", mode=mode, mode_note=mode_note,
        )]
        parts.append(f"Workspace root: {self.root}")
        parts.append(
            "Available tools: " + ", ".join(
                t["function"]["name"] for t in tools.TOOL_SCHEMAS
                if plan and t["function"]["name"] in PLAN_DENY
            ) + "."
        )
        agents_md = self.root / "AGENTS.md"
        if agents_md.is_file():
            try:
                parts.append(
                    "Project instructions (AGENTS.md) — follow these:\n"
                    + agents_md.read_text(encoding="utf-8", errors="replace")[:12_000]
                )
            except Exception:
                pass
        else:
            parts.append(
                "No AGENTS.md yet. If the user asks you to analyze the project or says /init, "
                "create AGENTS.md at the workspace root describing structure, stack and conventions."
            )
        extra = (cfg.get("systemPrompt") or "").strip()
        if extra:
            parts.append(extra)
        if self.ctx.todos:
            lines = "\n".join(
                f"- [{t['status']}] {t['content']}" for t in self.ctx.todos
            )
            parts.append("Current todo list:\n" + lines)
        return "\n\n".join(parts)

    def build_messages(self, history_window=None):
        msgs = [{"role": "system", "content": self.system_prompt()}]
        for m in self.session.messages:
            role = m.get("role")
            if role not in ("user", "assistant", "tool"):
                continue
            entry = {"role": role, "content": m.get("content") or ""}
            if m.get("tool_calls"):
                entry["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                entry["tool_call_id"] = m["tool_call_id"]
            if m.get("name"):
                entry["name"] = m["name"]
            if not entry["content"] and not m.get("tool_calls"):
                entry["content"] = "(empty)"
            msgs.append(entry)
        if history_window and len(msgs) > history_window:
            head = msgs[0]
            msgs = [head] + msgs[-history_window:]
        return msgs

    def check_permission(self, name, args):
        if self.session.mode == "plan" and name in PLAN_DENY:
            self.emit("permission", {
                "tool": name, "args": args, "allowed": False, "mode": "plan",
                "reason": f"{name} is not available in Plan mode (read-only).",
            })
            return False
        cat = tools.PERMISSION_CATEGORY.get(name, "read")
        setting = (self.cfg.get("permissions") or {}).get(cat, "allow")
        if setting == "allow":
            return True
        if setting == "deny":
            self.emit("permission", {
                "tool": name, "args": args, "allowed": False,
                "reason": f"{cat} is denied in settings.",
            })
            return False
        preview = self.describe(name, args)
        self.emit("permission", {
            "tool": name, "category": cat, "args": args, "preview": preview,
            "pending": True,
        })
        answer = self.state.request_permission({
            "tool": name, "category": cat, "preview": preview, "args": args,
        })
        allowed = bool(answer.get("allowed"))
        if allowed and answer.get("remember"):
            perms = self.cfg.setdefault("permissions", {})
            perms[cat] = "allow"
            from .config import save_config
            save_config(self.cfg)
        return allowed

    def describe(self, name, args):
        a = args or {}
        if name == "bash":
            return "$ " + str(a.get("command", ""))
        if name == "write":
            content = str(a.get("content", ""))
            preview = "\n".join(content.splitlines()[:25])
            return f"write {a.get('path')} ({len(content)} chars)\n{preview}"
        if name == "edit":
            return (
                f"edit {a.get('path')}\n"
                f"- {str(a.get('old_string', ''))[:300]}\n"
                f"+ {str(a.get('new_string', ''))[:300]}"
            )
        if name in ("read", "ls", "glob", "grep", "webfetch", "websearch"):
            return f"{name} {a.get('path') or a.get('pattern') or a.get('url') or a.get('query') or ''}"
        if name == "todowrite":
            todos = a.get("todos") or []
            return "set todo list:\n" + "\n".join(
                f"- [{t.get('status','pending')}] {t.get('content','')}"
                for t in todos[:12] if isinstance(t, dict)
            )
        return name

    def run(self, user_text=None, command=None):
        t0 = time.time()
        if command:
            handled = self.handle_command(command)
            if handled is not None:
                return handled
        if user_text:
            self.session.append("user", user_text)
            self.session.save()
            self.emit("user", {"text": user_text})

        provider = self.cfg.get("provider")
        model = self.cfg.get("model")
        max_steps = int(self.cfg.get("maxSteps", 40))
        self.emit("status", {
            "phase": "thinking", "provider": provider, "model": model,
            "session": self.session.id,
        })

        for step in range(max_steps):
            self.state.check_abort()
            self.emit("status", {"phase": "streaming", "step": step + 1})
            messages = self.build_messages()
            emitted = {"text": "", "think": ""}

            def on_delta(chunk, _e=emitted):
                _e["text"] += chunk
                self.emit("delta", {"text": chunk})

            def on_think(chunk, _e=emitted):
                _e["think"] += chunk
                self.emit("thinking", {"text": chunk})

            try:
                res = llm.chat_stream(
                    provider, self.cfg, messages, tools.TOOL_SCHEMAS,
                    model=model, on_delta=on_delta, on_think=on_think,
                    signal=self.state.abort,
                )
            except llm.LLMError as e:
                self.emit("error", {"message": str(e)[:900]})
                self.session.append("assistant", f"Error: {e}")
                self.session.save()
                return
            except Exception as e:
                self.emit("error", {"message": f"{type(e).__name__}: {e}"})
                return

            text = (res.get("content") or emitted["text"] or "").strip()
            if not text and res.get("reasoning"):
                text = res["reasoning"].strip()
            if text:
                self.session.append("assistant", text)
            if res.get("tool_calls"):
                self.session.append("assistant", text, tool_calls=res["tool_calls"])
            self.emit("assistant_done", {
                "text": text,
                "usage": res.get("usage"),
                "finish": res.get("finish_reason"),
            })
            self.session.save()

            if not res.get("tool_calls"):
                if res.get("finish_reason") == "length":
                    self.emit("error", {
                        "message": "model hit the output token limit before answering. "
                                   "Raise max tokens or use a smaller task."
                    })
                break

            for tc in res["tool_calls"]:
                self.state.check_abort()
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.emit("tool_start", {
                    "id": tc["id"], "tool": name, "args": args,
                    "title": self.describe(name, args),
                })
                allowed = self.check_permission(name, args)
                if not allowed:
                    reason = "denied by user"
                    self.emit("tool_end", {
                        "id": tc["id"], "tool": name, "ok": False,
                        "output": reason, "denied": True,
                    })
                    self.session.append(
                        "tool", reason, tool_call_id=tc["id"], name=name,
                        args=args, denied=True,
                    )
                    continue
                output = tools.run_tool(self.ctx, name, args)
                ok = not str(output).startswith("error:")
                self.emit("tool_end", {
                    "id": tc["id"], "tool": name, "ok": ok,
                    "output": str(output)[:20000],
                })
                self.session.append(
                    "tool", str(output)[:20000], tool_call_id=tc["id"],
                    name=name, args=args, ok=ok,
                )
                if name == "todowrite":
                    self.emit_todos()
                self.session.save()
            self.session.save()
        else:
            self.emit("error", {"message": f"stopped: max steps ({max_steps}) reached"})

        self.emit("done", {
            "ms": int((time.time() - t0) * 1000),
            "session": self.session.id,
            "todos": self.ctx.todos,
        })
        self.session.save()

    def handle_command(self, cmd):
        c = cmd.strip()
        low = c.lower()
        if low in ("/clear", "clear"):
            self.session.messages = []
            self.session.todos = []
            self.ctx.todos = []
            self.session.title = "New session"
            self.session.save()
            self.emit("cleared", {})
            self.emit("done", {"ms": 0, "session": self.session.id, "todos": []})
            return True
        if low in ("/undo", "undo"):
            out = self.session.undo_last()
            self.emit("command_result", {"text": out})
            self.emit("done", {"ms": 0, "session": self.session.id,
                               "todos": self.ctx.todos})
            return True
        if low in ("/redo", "redo"):
            out = self.session.redo_last()
            self.emit("command_result", {"text": out})
            self.emit("done", {"ms": 0, "session": self.session.id,
                               "todos": self.ctx.todos})
            return True
        if low in ("/plan", "plan"):
            self.session.mode = "plan"
            self.session.save()
            self.emit("mode", {"mode": "plan"})
            self.emit("done", {"ms": 0, "session": self.session.id,
                               "todos": self.ctx.todos})
            return True
        if low in ("/build", "build"):
            self.session.mode = "build"
            self.session.save()
            self.emit("mode", {"mode": "build"})
            self.emit("done", {"ms": 0, "session": self.session.id,
                               "todos": self.ctx.todos})
            return True
        if low in ("/init", "init"):
            text = (
                "Analyze this workspace and create an AGENTS.md file at the root. "
                "Use todowrite to track the steps, read the key files, then write AGENTS.md "
                "with: project purpose, tech stack, directory layout, code conventions, "
                "how to run tests and build, and any rules an agent must follow. "
                "Keep it under 150 lines and concrete, no generic advice."
            )
            self.emit("user", {"text": text, "synthetic": True})
            self.session.append("user", text)
            self.session.save()
            self.run()
            return True
        if low in ("/share", "share"):
            data = self.session.to_dict()
            self.emit("share", {"session": self.session.id,
                                "json": json.dumps(data, ensure_ascii=False)})
            self.emit("done", {"ms": 0, "session": self.session.id,
                               "todos": self.ctx.todos})
            return True
        if low in ("/help", "help", "/commands"):
            self.emit("command_result", {"text": (
                "/init — analyze workspace and write AGENTS.md\n"
                "/plan — read-only planning mode\n"
                "/build — allow edits and commands\n"
                "/undo — revert the last file change\n"
                "/redo — re-apply it\n"
                "/clear — clear this session\n"
                "/share — export session JSON\n"
                "/help — this list\n"
                "\nshortcuts: Tab mode · @ file · Ctrl+C interrupt"
            )})
            self.emit("done", {"ms": 0, "session": self.session.id,
                               "todos": self.ctx.todos})
            return True
        return None
