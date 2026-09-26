import difflib
import fnmatch
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import BINARY_EXT, IGNORE_DIRS

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file from the workspace. Returns content with line numbers. Use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to workspace root"},
                    "offset": {"type": "integer", "description": "Start line (1-based)"},
                    "limit": {"type": "integer", "description": "Max lines to read"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create a new file or completely overwrite an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace an exact string in a file. old_string must match exactly and be unique unless replace_all is true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace directory. Use for git, tests, builds, listing. 30s timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "seconds, max 120"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List files and directories in a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern, e.g. **/*.py",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regex. Returns matching lines with file:line format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string", "description": "filter e.g. *.py"},
                    "ignore_case": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "webfetch",
            "description": "Fetch a URL and return its text content.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "websearch",
            "description": "Search the web and return result titles and URLs.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todowrite",
            "description": "Update the visible todo list for this task. Use proactively for multi-step work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
]

READ_ONLY = {"read", "ls", "glob", "grep", "webfetch", "websearch", "todowrite"}
MUTATING = {"write", "edit", "bash"}

PERMISSION_CATEGORY = {
    "read": "read", "ls": "read", "glob": "read", "grep": "read",
    "todowrite": "read",
    "webfetch": "web", "websearch": "web",
    "write": "edit", "edit": "edit", "bash": "bash",
}

MAX_READ_BYTES = 400_000
MAX_BASH_TIMEOUT = 120


class ToolError(Exception):
    pass


def safe_path(root, rel):
    if rel is None:
        raise ToolError("path is required")
    rel = str(rel).strip()
    if not rel:
        raise ToolError("path is required")
    p = Path(rel)
    if p.is_absolute():
        candidate = p
    else:
        candidate = (root / p).resolve()
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        raise ToolError(f"path escapes workspace: {rel}")
    return candidate


def rel_path(root, path):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _is_binary(path):
    return Path(path).suffix.lower() in BINARY_EXT


def _iter_files(root, sub=None, limit=4000):
    base = root if sub is None else safe_path(root, sub)
    if base.is_file():
        yield base
        return
    count = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in IGNORE_DIRS and not d.startswith(".") or d in {".github"}
        )
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            count += 1
            if count > limit:
                return
            yield p


def tool_read(ctx, path, offset=None, limit=None):
    root = ctx.root
    p = safe_path(root, path)
    if not p.exists():
        raise ToolError(f"file not found: {rel_path(root, p)}")
    if p.is_dir():
        raise ToolError(f"{rel_path(root, p)} is a directory, use ls")
    if _is_binary(p):
        return f"binary file ({p.stat().st_size} bytes) — cannot read as text"
    data = p.read_bytes()[:MAX_READ_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(offset or 1))
    end = start + int(limit) - 1 if limit else len(lines)
    chunk = lines[start - 1:end]
    width = len(str(min(end, len(lines))))
    body = "\n".join(
        f"{str(start + i).rjust(width)}\t{ln}" for i, ln in enumerate(chunk)
    )
    truncated = "" if end >= len(lines) else f"\n... ({len(lines) - end} more lines)"
    header = f"{rel_path(root, p)} ({len(lines)} lines, {p.stat().st_size} bytes)"
    return f"{header}\n\n{body}{truncated}"


def tool_write(ctx, path, content):
    root = ctx.root
    p = safe_path(root, path)
    existed = p.exists()
    before = p.read_text(encoding="utf-8", errors="replace") if existed else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", encoding="utf-8")
    ctx.snapshot(rel_path(root, p), before, content or "")
    verb = "Updated" if existed else "Created"
    lines = (content or "").count("\n") + 1
    return f"{verb} {rel_path(root, p)} ({lines} lines, {p.stat().st_size} bytes)"


def tool_edit(ctx, path, old_string, new_string, replace_all=False):
    root = ctx.root
    p = safe_path(root, path)
    if not p.exists():
        raise ToolError(f"file not found: {rel_path(root, p)}")
    if _is_binary(p):
        raise ToolError("cannot edit binary file")
    text = p.read_text(encoding="utf-8", errors="replace")
    if old_string is None or old_string == "":
        raise ToolError("old_string is required for edit")
    occurrences = text.count(old_string)
    if occurrences == 0:
        preview = old_string[:120].replace("\n", "\\n")
        raise ToolError(f"old_string not found in {rel_path(root, p)}: {preview!r}")
    if occurrences > 1 and not replace_all:
        raise ToolError(
            f"old_string appears {occurrences} times in {rel_path(root, p)} — "
            "add more context or set replace_all=true"
        )
    ctx.snapshot(rel_path(root, p), text, new_text)
    new_text = text.replace(old_string, new_string or "", -1 if replace_all else 1)
    p.write_text(new_text, encoding="utf-8")
    diff = list(difflib.unified_diff(
        text.splitlines(), new_text.splitlines(),
        fromfile=rel_path(root, p), tofile=rel_path(root, p), lineterm="", n=2,
    ))
    n_add = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    n_del = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    return f"Edited {rel_path(root, p)} (+{n_add} -{n_del})\n" + "\n".join(diff[:120])


def tool_bash(ctx, command, timeout=None):
    root = ctx.root
    secs = min(int(timeout or 30), MAX_BASH_TIMEOUT)
    shell = True
    if os.name == "nt":
        cmd = command
    else:
        cmd = command
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, shell=shell, cwd=str(root), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=secs,
        )
    except subprocess.TimeoutExpired:
        return f"command timed out after {secs}s"
    except Exception as e:
        raise ToolError(f"failed to run command: {type(e).__name__}: {e}")
    dt = time.time() - start
    out = (proc.stdout or "")[:60_000]
    err = (proc.stderr or "")[:30_000]
    parts = [f"$ {command}", f"exit={proc.returncode} time={dt:.1f}s"]
    if out.strip():
        parts.append("stdout:\n" + out.rstrip())
    if err.strip():
        parts.append("stderr:\n" + err.rstrip())
    if not out.strip() and not err.strip():
        parts.append("(no output)")
    return "\n".join(parts)


def tool_ls(ctx, path=None):
    root = ctx.root
    p = root if not path or path in (".", "/") else safe_path(root, path)
    if not p.exists():
        raise ToolError(f"not found: {rel_path(root, p)}")
    if p.is_file():
        return f"{rel_path(root, p)} (file, {p.stat().st_size} bytes)"
    rows = []
    for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if entry.name in IGNORE_DIRS:
            continue
        if entry.is_dir():
            n = sum(1 for _ in entry.iterdir())
            rows.append(f"  {entry.name}/  ({n} entries)")
        else:
            rows.append(f"  {entry.name}  ({entry.stat().st_size} bytes)")
    head = f"{rel_path(root, p) or '.'}/ ({len(rows)} entries)"
    return head + "\n" + "\n".join(rows[:400])


def tool_glob(ctx, pattern, path=None):
    root = ctx.root
    base = root if not path or path in (".", "/") else safe_path(root, path)
    if not base.exists():
        raise ToolError(f"not found: {rel_path(root, p2s(path))}")
    hits = []
    for f in _iter_files(root, None if not path or path in (".", "/") else path):
        rp = rel_path(root, f)
        if fnmatch.fnmatch(rp, pattern) or fnmatch.fnmatch(f.name, pattern):
            hits.append(rp)
        elif pattern.startswith("**/") and fnmatch.fnmatch(f.name, pattern[3:]):
            hits.append(rp)
    if not hits:
        return f"no files match {pattern}"
    return f"{len(hits)} matches for {pattern}\n" + "\n".join(sorted(hits)[:200])


def p2s(p):
    return p or "."


def tool_grep(ctx, pattern, path=None, glob=None, ignore_case=False):
    root = ctx.root
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}")
    matches = []
    scanned = 0
    for f in _iter_files(root, None if not path or path in (".", "/") else path):
        rp = rel_path(root, f)
        if glob and not fnmatch.fnmatch(f.name, glob) and not fnmatch.fnmatch(rp, glob):
            continue
        if _is_binary(f):
            continue
        try:
            if f.stat().st_size > 1_500_000:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        scanned += 1
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append(f"{rp}:{i}: {line.strip()[:300]}")
                if len(matches) >= 300:
                    return "\n".join(matches) + "\n... (truncated at 300 matches)"
    if not matches:
        return f"no matches for {pattern!r} (scanned {scanned} files)"
    return "\n".join(matches)


def tool_webfetch(ctx, url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError("only http/https URLs supported")
    host = (parsed.hostname or "").lower()
    blocked = {"169.254.169.254", "metadata.google.internal"}
    if host in blocked:
        raise ToolError("blocked host")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; opencode-web/2.0)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(300_000)
            ctype = r.headers.get("Content-Type", "")
    except Exception as e:
        raise ToolError(f"fetch failed: {type(e).__name__}: {e}")
    text = raw.decode("utf-8", errors="replace")
    if "html" in ctype.lower() or text.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = urllib.parse.unquote(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
    return f"# {url}\n\n{text.strip()[:25_000]}"


def tool_websearch(ctx, query):
    url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(200_000).decode("utf-8", errors="replace")
    except Exception as e:
        raise ToolError(f"search failed: {type(e).__name__}: {e}")
    results = []
    for m in re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', html
    ):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        results.append(f"- {urllib.parse.unquote(title).strip()} — {href}")
        if len(results) >= 10:
            break
    if not results:
        return f"no results for {query!r}"
    return f"search: {query}\n" + "\n".join(results)


def tool_todowrite(ctx, todos):
    if not isinstance(todos, list):
        raise ToolError("todos must be an array")
    clean = []
    for t in todos:
        if not isinstance(t, dict):
            continue
        clean.append({
            "content": str(t.get("content", ""))[:200],
            "status": t.get("status") if t.get("status") in
            ("pending", "in_progress", "completed") else "pending",
            "priority": t.get("priority") if t.get("priority") in
            ("low", "medium", "high") else "medium",
        })
    ctx.todos = clean
    done = sum(1 for t in clean if t["status"] == "completed")
    return f"todo list updated: {done}/{len(clean)} completed"


HANDLERS = {
    "read": tool_read,
    "write": tool_write,
    "edit": tool_edit,
    "bash": tool_bash,
    "ls": tool_ls,
    "glob": tool_glob,
    "grep": tool_grep,
    "webfetch": tool_webfetch,
    "websearch": tool_websearch,
    "todowrite": tool_todowrite,
}


def run_tool(ctx, name, args):
    fn = HANDLERS.get(name)
    if not fn:
        return f"error: unknown tool {name}"
    try:
        return fn(ctx, **(args or {}))
    except ToolError as e:
        return f"error: {e}"
    except TypeError as e:
        return f"error: bad arguments for {name}: {e}"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"
