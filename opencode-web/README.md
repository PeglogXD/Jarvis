# opencode-web 2.0

An open source AI coding agent with a real terminal-style UI. It is not a mockup:
the model actually calls tools, and the tools really read, write and run things in
your project.

```
opencode-web/
  server.py        HTTP + SSE server (Python stdlib only, no pip install needed)
  core/
    config.py      providers, keys, config load/save
    llm.py         OpenAI-compatible client with SSE streaming
    tools.py       read, write, edit, bash, ls, glob, grep, webfetch, websearch, todowrite
    permissions.py permission model (allow / ask / deny per category)
    session.py     sessions, undo/redo snapshots, run state
    agent.py       the agent loop
  web/             the TUI (index.html, app.js, style.css)
  sessions/        conversation history (JSON)
  config.json      your settings
```

## Run it

Windows:

```
iniciar.bat
```

macOS / Linux / Chromebook (Linux):

```
chmod +x iniciar.sh
./iniciar.sh
```

Then open <http://127.0.0.1:7791>.

No dependencies. Python 3.9+ standard library only.

## API keys

Keys are read from `opencode-web/.env`, then `../.env` (the parent folder), then
OS environment variables:

```
NVIDIA_API_KEY=nvapi-...
GEMINI_API_KEY=...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
OPENCODE_API_KEY=...        # OpenCode Zen
```

Ollama needs no key: pick the `ollama` provider, model `qwen3-coder:30b`.

You can also paste a key in Settings instead of using a file.

## Modes

- **build** — the agent may edit files and run commands.
- **plan** — read-only. `write`, `edit` and `bash` are refused; the agent can only
  investigate and propose a plan. Toggle with `Tab`.

## Permissions

Each tool category (`read`, `edit`, `bash`, `web`) is `allow`, `ask` or `deny`.
With `ask` the agent pauses and the UI shows the exact command or diff, with
`allow once`, `always allow` and `deny`. `always allow` is remembered per project.

## Commands

| command | what it does |
| --- | --- |
| `/init` | analyze the workspace and write `AGENTS.md` |
| `/plan` `/build` | switch mode |
| `/undo` `/redo` | revert or re-apply the last file change |
| `/clear` | clear the session |
| `/share` | export the session as JSON |
| `/help` | list commands |

Keys: `Tab` mode, `@` mention a file, `/` command, `↑` history, `Ctrl+C` interrupt,
`Ctrl+N` new session.

## Safety

- Every tool path is resolved and confined to the workspace root. Paths that escape
  are rejected.
- Writes and shell commands require approval by default.
- The server binds to `127.0.0.1` only, so it is not reachable from the network.
- `bash` has a 30s timeout, capped at 120s.

## Chromebook / another machine

The agent needs Python and a key, so it runs on the machine that holds the project.
Two options:

**Run it on the Chromebook (fully independent of your PC).**
Enable Linux support in ChromeOS, copy this folder over, then:

```
sudo apt update && sudo apt install python3
cd opencode-web
echo "NVIDIA_API_KEY=nvapi-..." > .env
./iniciar.sh
```

Then open <http://127.0.0.1:7791> in Chrome. This works with your PC switched off.

**Reach your PC from the Chromebook on the same Wi-Fi.**

```
set OPENCODE_WEB_HOST=0.0.0.0
iniciar.bat
```

and open `http://<your-pc-ip>:7791` on the Chromebook. This only works while the PC
is on, and it exposes the agent to your whole network, so use it on a trusted
network only.

## Configuration

`config.json`:

```json
{
  "provider": "nvidia",
  "model": "nvidia/nemotron-3-super-120b-a12b",
  "workspace": "C:/path/to/your/project",
  "permissions": { "read": "allow", "edit": "ask", "bash": "ask", "web": "allow" },
  "mode": "build",
  "maxSteps": 40,
  "temperature": 0.1
}
```

Point `workspace` at any project and the agent will work in it.
